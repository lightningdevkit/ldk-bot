import hmac
import hashlib
import logging
import requests
from models import PullRequest, Review
from datetime import datetime, timedelta

class GitHubBot:
    def __init__(self, token, webhook_secret, db):
        self.token = token
        self.webhook_secret = webhook_secret.encode()
        self.db = db
        self.logger = logging.getLogger(__name__)
        self.headers = {
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github.v3+json'
        }

    def verify_webhook(self, signature, payload):
        """Verify webhook signature."""
        if not signature:
            return False

        expected = hmac.new(
            self.webhook_secret,
            payload,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(f"sha256={expected}", signature)

    def handle_pr_event(self, data):
        """Handle pull request events."""
        action = data.get('action')
        pr = data.get('pull_request')

        if not pr:
            return

        if action == 'opened':
            self._handle_new_pr(pr)
        elif action == 'closed':
            self._handle_closed_pr(pr)

    def _handle_new_pr(self, pr):
        """Handle new pull request."""
        repo_url = pr['base']['repo']['url']
        repo_name = pr['base']['repo']['full_name']
        pr_number = pr['number']

        # Create new PR record
        new_pr = PullRequest(
            pr_number=pr_number,
            repo_name=repo_name,
            title=pr['title'],
            status='pending_reviewer_choice',
            created_at=datetime.utcnow(),
            reminder_count=0
        )
        self.db.session.add(new_pr)
        self.db.session.commit()

        comment = (
            "👋 Hi! Would you like to pick specific reviewers for this PR? "
            "If yes, please mention them in a comment. "
            "If not, I'll automatically assign reviewers for you. "
            "Please respond within 24 hours."
        )

        self._create_comment(repo_url, pr_number, comment)

    def _handle_closed_pr(self, pr):
        """Handle closed pull request."""
        pr_record = PullRequest.query.filter_by(
            pr_number=pr['number'],
            repo_name=pr['base']['repo']['full_name']
        ).first()

        if pr_record:
            pr_record.status = 'closed'
            self.db.session.commit()

    def handle_review_event(self, data):
        """Handle pull request review events."""
        review = data.get('review')
        pr = data.get('pull_request')

        if not review or not pr:
            return

        pr_record = PullRequest.query.filter_by(
            pr_number=pr['number'],
            repo_name=pr['base']['repo']['full_name']
        ).first()

        if not pr_record:
            return

        new_review = Review(
            pr_id=pr_record.id,
            reviewer=review['user']['login'],
            status=review['state']
        )
        self.db.session.add(new_review)
        self.db.session.commit()

        if review['state'] == 'approved':
            self._handle_approved_review(pr)
        elif review['state'] == 'changes_requested':
            self._handle_changes_requested(pr)

    def _handle_approved_review(self, pr):
        """Handle approved review."""
        comment = (
            "✅ This PR has been approved! "
            "Would you like another round of review? "
            "Please let me know in a comment."
        )
        self._create_comment(pr['base']['repo']['url'], pr['number'], comment)

    def _handle_changes_requested(self, pr):
        """Handle changes requested review."""
        comment = (
            "📝 Changes have been requested. "
            "Please address the feedback and let me know when you're ready for another review."
        )
        self._create_comment(pr['base']['repo']['url'], pr['number'], comment)

    def _create_comment(self, repo_url, pr_number, body):
        """Create a comment on a PR."""
        comments_url = f"{repo_url}/issues/{pr_number}/comments"
        response = requests.post(
            comments_url,
            headers=self.headers,
            json={'body': body}
        )
        if response.status_code != 201:
            self.logger.error(f"Failed to create comment: {response.text}")

    def get_stats(self):
        """Get bot statistics."""
        active_prs = PullRequest.query.filter(
            PullRequest.status != 'closed'
        ).count()
        total_reviews = Review.query.count()

        return {
            'active_prs': active_prs,
            'total_reviews': total_reviews
        }

    def check_and_send_reminders(self):
        """Check for PRs needing review reminders and send them."""
        self.logger.info("Checking for PRs needing review reminders...")

        # Get PRs that need reminders (24 hours since last reminder or PR creation)
        current_time = datetime.utcnow()
        reminder_threshold = current_time - timedelta(hours=24)

        prs_needing_reminders = PullRequest.query.filter(
            PullRequest.status != 'closed',
            (
                (PullRequest.last_reminder_sent.is_(None) & (PullRequest.created_at <= reminder_threshold)) |
                (PullRequest.last_reminder_sent <= reminder_threshold)
            )
        ).all()

        for pr in prs_needing_reminders:
            self._send_review_reminder(pr)

    def _send_review_reminder(self, pr):
        """Send a reminder comment on a PR."""
        try:
            # Get assigned reviewers for the PR
            repo_url = f"https://api.github.com/repos/{pr.repo_name}"
            pr_url = f"{repo_url}/pulls/{pr.pr_number}"

            response = requests.get(pr_url, headers=self.headers)
            if response.status_code != 200:
                self.logger.error(f"Failed to fetch PR data: {response.text}")
                return

            pr_data = response.json()
            reviewers = [user['login'] for user in pr_data.get('requested_reviewers', [])]

            if not reviewers:
                self.logger.info(f"No reviewers to remind for PR #{pr.pr_number}")
                return

            # Create reminder message tagging all reviewers
            reviewer_tags = ' '.join([f'@{reviewer}' for reviewer in reviewers])
            reminder_count = pr.reminder_count + 1
            ordinal = lambda n: "%d%s" % (n,"tsnrhtdd"[(n//10%10!=1)*(n%10<4)*n%10::4])

            message = (
                f"🔔 {ordinal(reminder_count)} Reminder\n\n"
                f"Hey {reviewer_tags}! This PR has been waiting for your review.\n"
                "Please take a look when you have a chance. If you're unable to review, "
                "please let us know so we can find another reviewer."
            )

            # Post the reminder comment
            self._create_comment(repo_url, pr.pr_number, message)

            # Update reminder tracking
            pr.last_reminder_sent = datetime.utcnow()
            pr.reminder_count = reminder_count
            self.db.session.commit()

            self.logger.info(f"Sent review reminder for PR #{pr.pr_number}")

        except Exception as e:
            self.logger.error(f"Error sending reminder for PR #{pr.pr_number}: {str(e)}")