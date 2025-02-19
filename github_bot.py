import hmac
import hashlib
import logging
import requests
from pr_manager import PRManager

class GitHubBot:
    def __init__(self, token, webhook_secret):
        self.token = token
        self.webhook_secret = webhook_secret.encode()
        self.pr_manager = PRManager()
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
            self.pr_manager.remove_pr(pr['number'])

    def _handle_new_pr(self, pr):
        """Handle new pull request."""
        repo_url = pr['base']['repo']['url']
        pr_number = pr['number']

        comment = (
            "👋 Hi! Would you like to pick specific reviewers for this PR? "
            "If yes, please mention them in a comment. "
            "If not, I'll automatically assign reviewers for you. "
            "Please respond within 24 hours."
        )

        self._create_comment(repo_url, pr_number, comment)
        self.pr_manager.add_pr(pr_number, pr)

    def handle_review_event(self, data):
        """Handle pull request review events."""
        review = data.get('review')
        pr = data.get('pull_request')

        if not review or not pr:
            return

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
        return {
            'active_prs': len(self.pr_manager.prs),
            'total_reviews': self.pr_manager.total_reviews
        }