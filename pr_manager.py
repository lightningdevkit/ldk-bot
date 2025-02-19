class PRManager:
    def __init__(self):
        self.prs = {}
        self.total_reviews = 0

    def add_pr(self, pr_number, pr_data):
        """Add a new PR to track."""
        self.prs[pr_number] = {
            'data': pr_data,
            'reviews': [],
            'status': 'pending_reviewer_choice'
        }

    def remove_pr(self, pr_number):
        """Remove a PR from tracking."""
        if pr_number in self.prs:
            del self.prs[pr_number]

    def get_pr(self, pr_number):
        """Get PR data by number."""
        return self.prs.get(pr_number, {}).get('data')

    def add_review(self, pr_number, reviewer, status):
        """Add a review to a PR."""
        if pr_number in self.prs:
            self.prs[pr_number]['reviews'].append({
                'reviewer': reviewer,
                'status': status
            })
            self.total_reviews += 1

    def get_pr_status(self, pr_number):
        """Get PR status."""
        return self.prs.get(pr_number, {}).get('status')

    def update_pr_status(self, pr_number, new_status):
        """Update PR status."""
        if pr_number in self.prs:
            self.prs[pr_number]['status'] = new_status