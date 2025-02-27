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
        elif action == 'ready_for_review':
            # Handle PR being converted from draft to ready
            pr_record = PullRequest.query.filter_by(
                pr_number=pr['number'],
                repo_name=pr['base']['repo']['full_name']).first()

            if pr_record:
                pr_record.status = 'pending_reviewer_choice'
                self.db.session.commit()

                comment = (
                    "🎉 This PR is now ready for review!\n"
                    "Please choose at least one reviewer by assigning them on the right bar.\n"
                    "If no reviewers are assigned within 10 minutes, I'll automatically assign one.\n"
                    "Once the first reviewer has submitted a review, a second will be assigned."
                )
                self._create_comment(pr['base']['repo']['url'], pr['number'], comment)

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

        app_url = f"https://{os.environ.get('REPL_SLUG')}.{os.environ.get('REPL_OWNER')}.repl.co"

        if pr.get('draft', False):
            comment = (
                "👋 Hi! I see this is a draft PR.\n"
                "I'll wait to assign reviewers until you mark it as ready for review.\n"
                "Just convert it out of draft status when you're ready for review!"
            )
            new_pr.status = 'draft'
            self.db.session.commit()
        else:
            comment = (
                "👋 Hi! Please choose at least one reviewer by assigning them on the right bar.\n"
                "If no reviewers are assigned within 10 minutes, I'll automatically assign one.\n"
                "Once the first reviewer has submitted a review, a second will be assigned."
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

        # After first review, ask if a second reviewer is needed
        self._ask_for_second_reviewer(pr, pr_record, app_url)

    def assign_reviewers(self, repo_name, pr_number, reviewers):
        """Assign reviewers to a PR."""
        url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_number}/requested_reviewers"
        response = requests.post(url,
                                 headers=self.headers,
                                 json={'reviewers': reviewers})
        if response.status_code != 201:
            raise Exception(f"Failed to assign reviewers: {response.text}")

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
                reviewer_counts[reviewer_login] = reviewer_counts.get(
                    reviewer_login, 0) + 1

        return reviewer_counts

    def auto_assign_reviewers(self, pr_record):
        """Auto-assign reviewers to a PR based on workload."""
        try:
            # Get PR data including author and existing reviewers
            pr_url = f"https://api.github.com/repos/{pr_record.repo_name}/pulls/{pr_record.pr_number}"
            pr_response = requests.get(pr_url, headers=self.headers)
            if pr_response.status_code != 200:
                self.logger.error(
                    f"Failed to fetch PR data: {pr_response.text}")
                return
            pr_data = pr_response.json()

            # Check if PR already has reviewers assigned
            if pr_data.get('requested_reviewers') and len(
                    pr_data['requested_reviewers']) > 0:
                self.logger.info(
                    f"PR #{pr_record.pr_number} already has reviewers assigned, skipping auto-assignment"
                )
                # Update PR status to needs_review since it already has reviewers
                pr_record.status = 'needs_review'
                self.db.session.commit()
                return

            pr_author = pr_data['user']['login']

            # Get collaborators excluding PR author
            collaborators = [
                c for c in self.get_repo_collaborators(pr_record.repo_name)
                if c != pr_author
            ]

            if not collaborators:
                self.logger.error(
                    f"No eligible reviewers found for PR #{pr_record.pr_number}"
                )
                return

            reviewer_counts = self.get_reviewer_pr_counts(pr_record.repo_name)

            # Initialize counts for new collaborators
            for collaborator in collaborators:
                if collaborator not in reviewer_counts:
                    reviewer_counts[collaborator] = 0

            # Sort collaborators by PR count
            sorted_reviewers = sorted(collaborators,
                                      key=lambda x: reviewer_counts.get(x, 0))

            # Select only the first reviewer with least PRs
            selected_reviewers = sorted_reviewers[:1] if sorted_reviewers else []

            if selected_reviewers:
                self.assign_reviewers(pr_record.repo_name, pr_record.pr_number,
                                      selected_reviewers)
                pr_record.status = 'needs_review'
                self.db.session.commit()
                self.logger.info(
                    f"Auto-assigned first reviewer for PR #{pr_record.pr_number}: {selected_reviewers}"
                )

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
                    reviewer_threshold = current_time - timedelta(minutes=10)
                    reminder_threshold = current_time - timedelta(days=1)

                    # Force a new connection from the pool
                    self.db.session.remove()

                    # Find PRs needing reviewer assignment (no reviewers after 10 minutes)
                    prs_needing_assignment = PullRequest.query.filter(
                        PullRequest.status == 'pending_reviewer_choice',
                        PullRequest.created_at <= reviewer_threshold).all()

                    # Auto-assign reviewers for these PRs
                    for pr in prs_needing_assignment:
                        self.auto_assign_reviewers(pr)

                    # Original reminder logic
                    prs_needing_reminders = PullRequest.query.filter(
                        PullRequest.status != 'closed',
                        ((PullRequest.last_reminder_sent.is_(None) &
                          (PullRequest.created_at <= reminder_threshold)) |
                         (PullRequest.last_reminder_sent
                          <= reminder_threshold))).all()

                    for pr in prs_needing_reminders:
                        self._send_review_reminder(pr)

                    break  # Success, exit the retry loop

                except Exception as e:
                    retry_count += 1
                    self.logger.error(
                        f"Attempt {retry_count} failed: {str(e)}")
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

    def _ask_for_second_reviewer(self, pr, pr_record, app_url):
        """Check if this PR already has more than one reviewer assigned."""
        try:
            repo_url = f"https://api.github.com/repos/{pr_record.repo_name}"

            # Check if this PR already has more than one reviewer assigned
            pr_url = f"{repo_url}/pulls/{pr_record.pr_number}"
            response = requests.get(pr_url, headers=self.headers)
            if response.status_code != 200:
                self.logger.error(f"Failed to fetch PR data: {response.text}")
                return

            pr_data = response.json()
            current_reviewers = [
                user['login']
                for user in pr_data.get('requested_reviewers', [])
            ]

            # If there's already more than one reviewer, don't do anything
            if len(current_reviewers) > 1:
                return

        except Exception as e:
            self.logger.error(f"Error checking for second reviewer: {str(e)}")

    def assign_second_reviewer(self, repo_name, pr_number):
        """Assign a second reviewer to a PR."""
        try:
            pr_record = PullRequest.query.filter_by(
                pr_number=pr_number, repo_name=repo_name).first()

            if not pr_record:
                self.logger.error(
                    f"No PR record found for {repo_name}#{pr_number}")
                return False

            # Get PR data
            pr_url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_number}"
            response = requests.get(pr_url, headers=self.headers)
            if response.status_code != 200:
                self.logger.error(f"Failed to fetch PR data: {response.text}")
                return False

            pr_data = response.json()
            pr_author = pr_data['user']['login']
            current_reviewers = [
                user['login']
                for user in pr_data.get('requested_reviewers', [])
            ]

            # Get collaborators excluding PR author and current reviewers
            collaborators = [
                c for c in self.get_repo_collaborators(repo_name)
                if c != pr_author and c not in current_reviewers
            ]

            if not collaborators:
                self.logger.error(
                    f"No eligible second reviewers found for PR #{pr_number}")
                return False

            # Get reviewer workloads
            reviewer_counts = self.get_reviewer_pr_counts(repo_name)

            # Initialize counts for new collaborators
            for collaborator in collaborators:
                if collaborator not in reviewer_counts:
                    reviewer_counts[collaborator] = 0

            # Sort collaborators by PR count
            sorted_reviewers = sorted(collaborators,
                                      key=lambda x: reviewer_counts.get(x, 0))

            # Select the reviewer with least PRs
            selected_reviewer = sorted_reviewers[
                0] if sorted_reviewers else None

            if selected_reviewer:
                self.assign_reviewers(repo_name, pr_number,
                                      [selected_reviewer])
                self.logger.info(
                    f"Assigned second reviewer for PR #{pr_number}: {selected_reviewer}"
                )

                # Post a comment
                repo_url = f"https://api.github.com/repos/{repo_name}"
                comment = f"✅ Added second reviewer: @{selected_reviewer}"
                self._create_comment(repo_url, pr_number, comment)

                return True

            return False

        except Exception as e:
            self.logger.error(f"Error assigning second reviewer: {str(e)}")
            return False