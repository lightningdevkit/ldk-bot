import os
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

        expected = hmac.new(self.webhook_secret, payload,
                            hashlib.sha256).hexdigest()
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
        new_pr = PullRequest(pr_number=pr_number,
                             repo_name=repo_name,
                             title=pr['title'],
                             status='pending_reviewer_choice',
                             created_at=datetime.utcnow(),
                             reminder_count=0)
        self.db.session.add(new_pr)
        self.db.session.commit()

        app_url = "https://" + os.environ.get(
            'REPL_SLUG') + "." + os.environ.get('REPL_OWNER') + ".repl.co"
        [org, repo] = repo_name.split('/')
        comment = (
            f"👋 Hi! Please choose reviewers for this PR by visiting:\n"
            f"{app_url}/choose-reviewers/{org}/{repo}/{pr_number}\n\n"
            "If no reviewers are chosen within 10 minutes, I'll automatically assign them."
        )

        self._create_comment(repo_url, pr_number, comment)

    def _handle_closed_pr(self, pr):
        """Handle closed pull request."""
        pr_record = PullRequest.query.filter_by(
            pr_number=pr['number'],
            repo_name=pr['base']['repo']['full_name']).first()

        if pr_record:
            pr_record.status = 'closed'
            self.db.session.commit()

    def handle_review_event(self, data):
        """Handle pull request review events."""
        action = data.get('action')
        pr = data.get('pull_request')
        review = data.get('review')

        if action == 'review_requested':
            # Handle re-requesting review
            if pr:
                pr_record = PullRequest.query.filter_by(
                    pr_number=pr['number'],
                    repo_name=pr['base']['repo']['full_name']).first()
                
                if pr_record:
                    pr_record.status = 'needs_review'
                    self.db.session.commit()
                    self.request_review(pr_record)
            return

        if not review or not pr:
            self.logger.error("No review/PR in req!")
            return

        pr_record = PullRequest.query.filter_by(
            pr_number=pr['number'],
            repo_name=pr['base']['repo']['full_name']).first()

        if not pr_record:
            self.logger.error(f"No PR Record for: {pr['number']}")
            return

        new_review = Review(pr_id=pr_record.id,
                            reviewer=review['user']['login'],
                            status=review['state'])
        self.db.session.add(new_review)
        self.db.session.commit()

        # Update PR status to reviewed
        pr_record.status = 'reviewed'
        self.db.session.commit()

        # Update the initial bot comment
        app_url = "https://" + os.environ.get(
            'REPL_SLUG') + "." + os.environ.get('REPL_OWNER') + ".repl.co"
        comments_url = f"{pr['base']['repo']['url']}/issues/{pr['number']}/comments"

        # Get the bot's first comment
        response = requests.get(comments_url, headers=self.headers)
        self.logger.info(
            f"got response {response.status_code} to URL {comments_url}:\n{response.json()}"
        )
        if response.status_code == 200:
            bot_comments = [
                c for c in response.json()
                if c['user']['login'] == 'ldk-reviews-bot'
            ]
            if bot_comments:
                first_comment = bot_comments[0]
                update_url = f"{pr['base']['repo']['url']}/issues/comments/{first_comment['id']}"

                new_comment = (
                    "✅ This PR has been reviewed!\n\n"
                    f"To mark this PR as needing another review, visit:\n"
                    f"{app_url}/mark-needs-review/{pr_record.repo_name}/{pr_record.pr_number}"
                )

                requests.patch(update_url,
                               headers=self.headers,
                               json={'body': new_comment})

        if review['state'] == 'approved':
            self._handle_approved_review(pr)
        elif review['state'] == 'changes_requested':
            self._handle_changes_requested(pr)

    def _handle_approved_review(self, pr):
        """Handle approved review."""
        comment = ("✅ This PR has been approved! "
                   "Would you like another round of review? "
                   "Please let me know in a comment.")
        self._create_comment(pr['base']['repo']['url'], pr['number'], comment)

    def _handle_changes_requested(self, pr):
        """Handle changes requested review."""
        comment = (
            "📝 Changes have been requested. "
            "Please address the feedback and let me know when you're ready for another review."
        )
        self._create_comment(pr['base']['repo']['url'], pr['number'], comment)

    def assign_reviewers(self, repo_name, pr_number, reviewers):
        """Assign reviewers to a PR."""
        url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_number}/requested_reviewers"
        response = requests.post(url,
                                 headers=self.headers,
                                 json={'reviewers': reviewers})
        if response.status_code != 201:
            raise Exception(f"Failed to assign reviewers: {response.text}")

        comment = f"✅ Assigned reviewers: {', '.join(['@' + r for r in reviewers])}"
        self._create_comment(f"https://api.github.com/repos/{repo_name}",
                             pr_number, comment)

    def _create_comment(self, repo_url, pr_number, body):
        """Create a comment on a PR."""
        comments_url = f"{repo_url}/issues/{pr_number}/comments"
        response = requests.post(comments_url,
                                 headers=self.headers,
                                 json={'body': body})
        if response.status_code != 201:
            self.logger.error(f"Failed to create comment: {response.text}")

    def get_stats(self):
        """Get bot statistics."""
        active_prs = PullRequest.query.filter(
            PullRequest.status != 'closed').count()
        total_reviews = Review.query.count()

        return {'active_prs': active_prs, 'total_reviews': total_reviews}

    def get_repo_collaborators(self, repo_name):
        """Get list of collaborators for a repository."""
        url = f"https://api.github.com/repos/{repo_name}/collaborators"
        response = requests.get(url, headers=self.headers)
        if response.status_code != 200:
            self.logger.error(f"Failed to get collaborators: {response.text}")
            return []
        return [user['login'] for user in response.json()]

    def get_reviewer_pr_counts(self, repo_name):
        """Get count of open PRs assigned to each reviewer."""
        reviewer_counts = {}

        # Get all open PRs
        url = f"https://api.github.com/repos/{repo_name}/pulls"
        response = requests.get(url, headers=self.headers)
        if response.status_code != 200:
            self.logger.error(f"Failed to get PRs: {response.text}")
            return {}

        for pr in response.json():
            for reviewer in pr.get('requested_reviewers', []):
                reviewer_login = reviewer['login']
                reviewer_counts[reviewer_login] = reviewer_counts.get(reviewer_login, 0) + 1

        return reviewer_counts

    def auto_assign_reviewers(self, pr_record):
        """Auto-assign reviewers to a PR based on workload."""
        try:
            # Get PR author
            pr_url = f"https://api.github.com/repos/{pr_record.repo_name}/pulls/{pr_record.pr_number}"
            pr_response = requests.get(pr_url, headers=self.headers)
            if pr_response.status_code != 200:
                self.logger.error(f"Failed to fetch PR data: {pr_response.text}")
                return
            pr_data = pr_response.json()
            pr_author = pr_data['user']['login']

            # Get collaborators excluding PR author
            collaborators = [c for c in self.get_repo_collaborators(pr_record.repo_name) 
                           if c != pr_author]

            if not collaborators:
                self.logger.error(f"No eligible reviewers found for PR #{pr_record.pr_number}")
                return

            reviewer_counts = self.get_reviewer_pr_counts(pr_record.repo_name)

            # Initialize counts for new collaborators
            for collaborator in collaborators:
                if collaborator not in reviewer_counts:
                    reviewer_counts[collaborator] = 0

            # Sort collaborators by PR count
            sorted_reviewers = sorted(collaborators, key=lambda x: reviewer_counts.get(x, 0))

            # Select the two reviewers with least PRs, or all available if less than 2
            selected_reviewers = sorted_reviewers[:min(2, len(sorted_reviewers))]

            if selected_reviewers:
                self.assign_reviewers(pr_record.repo_name, pr_record.pr_number, selected_reviewers)
                pr_record.status = 'needs_review'
                self.db.session.commit()
                self.logger.info(f"Auto-assigned reviewers for PR #{pr_record.pr_number}: {selected_reviewers}")

        except Exception as e:
            self.logger.error(f"Error auto-assigning reviewers: {str(e)}")

    def check_and_send_reminders(self):
        """Check for PRs needing review reminders and auto-assign reviewers."""
        self.logger.info("Checking for PRs needing review reminders...")

        max_retries = 3
        retry_count = 0

        try:
            while retry_count < max_retries:
                try:
                    current_time = datetime.utcnow()
                    reminder_threshold = current_time - timedelta(minutes=10)

                    # Force a new connection from the pool
                    self.db.session.remove()

                    # Find PRs needing reviewer assignment (no reviewers after 10 minutes)
                    prs_needing_assignment = PullRequest.query.filter(
                        PullRequest.status == 'pending_reviewer_choice',
                        PullRequest.created_at <= reminder_threshold
                    ).all()

                    # Auto-assign reviewers for these PRs
                    for pr in prs_needing_assignment:
                        self.auto_assign_reviewers(pr)

                    # Original reminder logic
                    prs_needing_reminders = PullRequest.query.filter(
                        PullRequest.status != 'closed',
                        ((PullRequest.last_reminder_sent.is_(None) &
                          (PullRequest.created_at <= reminder_threshold)) |
                         (PullRequest.last_reminder_sent <= reminder_threshold))).all()

                    for pr in prs_needing_reminders:
                        self._send_review_reminder(pr)

                    break  # Success, exit the retry loop

                except Exception as e:
                    retry_count += 1
                    self.logger.error(f"Attempt {retry_count} failed: {str(e)}")
                    if retry_count == max_retries:
                        self.logger.error("Max retries reached, giving up")
                        raise
                    self.db.session.rollback()

        except Exception as e:
            self.logger.error(f"Error in reminder scheduler: {str(e)}")
            # Ensure the session is clean for the next run
            self.db.session.rollback()

    def request_review(self, pr_record):
        """Request reviews when PR is marked as needing review."""
        try:
            # Get assigned reviewers for the PR
            repo_url = f"https://api.github.com/repos/{pr_record.repo_name}"
            pr_url = f"{repo_url}/pulls/{pr_record.pr_number}"

            response = requests.get(pr_url, headers=self.headers)
            if response.status_code != 200:
                self.logger.error(f"Failed to fetch PR data: {response.text}")
                return

            pr_data = response.json()
            self.logger.info(f"Got {pr_data}")
            reviewers = [
                user['login']
                for user in pr_data.get('requested_reviewers', [])
            ]

            if reviewers:
                reviewer_tags = ' '.join(
                    [f'@{reviewer}' for reviewer in reviewers])
                message = (
                    f"👋 Hey {reviewer_tags}!\n\n"
                    "This PR has been marked as needing another review. "
                    "Could you please take another look when you have a chance?"
                )
                self._create_comment(repo_url, pr_record.pr_number, message)

        except Exception as e:
            self.logger.error(
                f"Error requesting review for PR #{pr_record.pr_number}: {str(e)}"
            )

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
            self.logger.info(f"Got {pr_data}")
            reviewers = [
                user['login']
                for user in pr_data.get('requested_reviewers', [])
            ]

            if not reviewers:
                self.logger.info(
                    f"No reviewers to remind for PR #{pr.pr_number}")
                return

            # Create reminder message tagging all reviewers
            reviewer_tags = ' '.join(
                [f'@{reviewer}' for reviewer in reviewers])
            reminder_count = pr.reminder_count + 1
            ordinal = lambda n: "%d%s" % (n, "tsnrhtdd"[
                (n // 10 % 10 != 1) * (n % 10 < 4) * n % 10::4])

            message = (
                f"🔔 {ordinal(reminder_count)} Reminder\n\n"
                f"Hey {reviewer_tags}! This PR has been waiting for your review.\n"
                "Please take a look when you have a chance. If you're unable to review, "
                "please let us know so we can find another reviewer.")

            # Post the reminder comment
            self._create_comment(repo_url, pr.pr_number, message)

            # Update reminder tracking
            pr.last_reminder_sent = datetime.utcnow()
            pr.reminder_count = reminder_count
            self.db.session.commit()

            self.logger.info(f"Sent review reminder for PR #{pr.pr_number}")

        except Exception as e:
            self.logger.error(
                f"Error sending reminder for PR #{pr.pr_number}: {str(e)}")