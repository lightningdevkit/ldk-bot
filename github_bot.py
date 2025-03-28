import os
import hmac
import hashlib
import logging
import requests
import random
from models import PullRequest, Review, PRStatus
from datetime import datetime, timedelta

APP_BASE_URL="https://ldk-reviews-bot.bluematt.me/"

MIN_PR_ID = { "lightningdevkit/rust-lightning": 3634, "lightningdevkit/ldk-node": 512 }

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

	def sync_existing_prs(self):
		"""Sync existing pull requests, reviews and assigned reviewers on startup."""
		self.logger.info("Starting sync of existing pull requests...")

		for repo_name in MIN_PR_ID:
			# Get all open pull requests
			url = f"https://api.github.com/repos/{repo_name}/pulls?state=all"
			response = requests.get(url, headers=self.headers)
			response.raise_for_status()

			prs = response.json()
			self.logger.info(f"Found {len(prs)} pull requests")

			for pr in prs:
				if pr['number'] < MIN_PR_ID[repo_name]:
					continue
				if pr['state'] == "open":
					# Check if PR already exists in database
					existing_pr = PullRequest.query.filter_by(
						pr_number=pr['number'], repo_name=repo_name).first()

					if not existing_pr:
						self._handle_new_pr(pr)
				else:
					# Check if PR already exists in database
					existing_pr = PullRequest.query.filter_by(
						pr_number=pr['number'], repo_name=repo_name).first()

					if existing_pr:
						existing_pr.status = PRStatus.CLOSED
						reviews = Review.query.filter_by(pr_number=pr['number'], repo_name=repo_name).all()
						for review in reviews:
							if review.completed_at is None:
								review.completed_at = datetime.utcnow()
						self.db.session.commit()

			self.db.session.commit()
			self.logger.info(f"Synced {len(prs)} PRs")

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
			self._handle_ready_for_review(pr)
		elif action == 'converted_to_draft':
			self._handle_converted_to_draft(pr)
		elif action == 'review_requested':
			self._handle_review_requested(data['pull_request'], data['requested_reviewer'])
		elif action == 'review_request_removed':
			self._handle_review_request_removed(data['pull_request'], data['requested_reviewer'])

	def _handle_new_pr(self, pr):
		"""Handle new pull request."""
		repo_url = pr['base']['repo']['url']
		repo_name = pr['base']['repo']['full_name']
		pr_number = pr['number']

		if pr_number < MIN_PR_ID[repo_name]:
			return

		# Create new PR record
		new_pr = PullRequest(pr_number=pr_number, repo_name=repo_name, pr_title = pr['title'],
								status=PRStatus.PENDING_REVIEWER_CHOICE)

		if pr.get('draft', False):
			comment = (
				"👋 Hi! I see this is a draft PR.\n"
				"I'll wait to assign reviewers until you mark it as ready for review.\n"
				"Just convert it out of draft status when you're ready for review!"
			)
			new_pr.status = PRStatus.DRAFT
		else:
			comment = (
				"👋 Hi! Please choose at least one reviewer by assigning them on the right bar.\n"
				"If no reviewers are assigned within 10 minutes, I'll automatically assign one.\n"
				"Once the first reviewer has submitted a review, a second will be assigned."
			)

		self.db.session.add(new_pr)
		self.db.session.commit() # commit here to ensure we dont comment on dup entries

		# Create initial comment and store its ID
		comment_id = self._create_comment(repo_url, pr_number, comment)
		if comment_id:
			new_pr.initial_comment_id = comment_id
		self.db.session.commit()

		for reviewer in pr['requested_reviewers']:
			self._handle_review_requested(pr, reviewer)

	def _handle_closed_pr(self, pr):
		"""Handle closed pull request."""
		repo_name = pr['base']['repo']['full_name']
		pr_record = PullRequest.query.filter_by(pr_number=pr['number'], repo_name=repo_name).first()

		if pr_record:
			pr_record.status = PRStatus.CLOSED
			reviews = Review.query.filter_by(pr_number=pr['number'], repo_name=repo_name).all()
			for review in reviews:
				if review.completed_at is None:
					self.db.session.delete(review)
			self.db.session.commit()

	def _handle_ready_for_review(self, pr):
		# Handle PR being converted from draft to ready
		repo_name = pr['base']['repo']['full_name']
		pr_record = PullRequest.query.filter_by(
			pr_number=pr['number'],
			repo_name=repo_name).first()

		if pr_record and pr_record.initial_comment_id:
			pr_record.status = PRStatus.PENDING_REVIEWER_CHOICE
			self.db.session.commit()

			comment = (
				"🎉 This PR is now ready for review!\n"
				"Please choose at least one reviewer by assigning them on the right bar.\n"
				"If no reviewers are assigned within 10 minutes, I'll automatically assign one.\n"
				"Once the first reviewer has submitted a review, a second will be assigned."
			)
			self._update_comment(pr['base']['repo']['url'], pr_record, comment)

		for reviewer in pr.get("requested_reviewers", []):
			self._add_pending_review(repo_name, pr['number'], reviewer["login"])

	def _handle_converted_to_draft(self, pr):
		"""Handle PR being converted to draft."""
		repo_name = pr['base']['repo']['full_name']
		pr_record = PullRequest.query.filter_by(
			pr_number=pr['number'],
			repo_name=repo_name).first()

		if pr_record:
			requests_completed = Review.query.filter(
				Review.pr_number == pr['number'],
				Review.repo_name == repo_name).all()
			for request in requests_completed:
				self.db.session.delete(request)

			if pr_record.initial_comment_id:
				# Update the initial comment
				comment = (
					"👋 Hi! This PR is now in draft status.\n"
					"I'll wait to assign reviewers until you mark it as ready for review.\n"
					"Just convert it out of draft status when you're ready for review!"
				)
				self._update_comment(pr['base']['repo']['url'], pr_record, comment)

			# Update PR status
			pr_record.status = PRStatus.DRAFT
			self.db.session.commit()

	def _handle_review_request_removed(self, pr, requested_reviewer):
		repo_name = pr['base']['repo']['full_name']
		pr_number = pr['number']
		repo_url = pr['base']['repo']['url']
		reviewer = requested_reviewer['login']

		if pr_number < MIN_PR_ID[repo_name]:
			return

		pr_record = PullRequest.query.filter_by(pr_number=pr_number, repo_name=repo_name).first()
		if pr_record is None:
			self.logger.error(f"Got a review-request-removed before PR #{pr_number} was open")
			return

		pending_review = Review.query.filter_by(pr_number=pr_number, repo_name=repo_name, reviewer=reviewer, completed_at=None).first()
		if pending_review:
			self.db.session.delete(pending_review)
			self.db.session.commit()

		assert pr_record.initial_comment_id is not None

		second_reviewer_url = f"{APP_BASE_URL}/assign-second-reviewer/{pr_record.repo_name}/{pr_record.pr_number}"

		# Update the initial comment
		comment = (
			f"👋 I see @{reviewer} was un-assigned.\n"
			f"If you'd like another reviewer assignemnt, please [click here]({second_reviewer_url})."
		)

		self._update_comment(repo_url, pr_record, comment)

	def _handle_review_requested(self, pr, requested_reviewer):
		"""Handle review requested event."""

		repo_name = pr['base']['repo']['full_name']
		pr_number = pr['number']
		reviewer = requested_reviewer['login']

		if pr_number < MIN_PR_ID[repo_name]:
			return

		pr_record = PullRequest.query.filter_by(pr_number=pr_number, repo_name=repo_name).first()
		if pr_record is None:
			self.logger.info(f"Got a review-request before PR #{pr_number} was open, probably it had assignment on open")
			return
		pr_record.status = PRStatus.PENDING_REVIEW

		pending_review = Review.query.filter_by(pr_number=pr_number, repo_name=repo_name, reviewer=reviewer, completed_at=None).first()
		if pending_review:
			# Probably we already assigned on the bot and then got the callback for the assignment
			self.db.session.commit()
			return

		assert pr_record.initial_comment_id is not None

		# Update the initial comment
		comment = (
			f"👋 Thanks for assigning @{reviewer} as a reviewer!\n"
			"I'll wait for their review and will help manage the review process.\n"
			"Once they submit their review, I'll check if a second reviewer would be helpful."
		)

		repo_url = pr['base']['repo']['url']
		self._update_comment(repo_url, pr_record, comment)

		# Update PR status
		self._add_pending_review(repo_name, pr_number, reviewer)

	def _add_pending_review(self, repo_name, pr_number, reviewer):
		pr_record = PullRequest.query.filter_by(pr_number=pr_number, repo_name=repo_name).first()
		assert pr_record is not None
		pr_record.status = PRStatus.PENDING_REVIEW

		pending_review = Review.query.filter_by(pr_number=pr_number, repo_name=repo_name, reviewer=reviewer, completed_at=None).first()
		if pending_review:
			# Probably we already assigned on the bot and then got the callback for the assignment
			self.db.session.commit()
			return

		new_review = Review(repo_name=repo_name, pr_number=pr_number, reviewer=reviewer)
		self.db.session.add(new_review)

		self.db.session.commit()

	def handle_review_event(self, data):
		"""Handle pull request review events."""
		action = data.get('action')
		pr = data.get('pull_request')
		review = data.get('review')
		repo_name = pr['base']['repo']['full_name']
		reviewer = review['user']['login']

		if pr['number'] < MIN_PR_ID[repo_name]:
			return

		if not review or not pr:
			self.logger.error("No review/PR in req!")
			return

		pr_author = pr['user']['login']
		if review['user']['login'] == pr_author:
			self.logger.info(f"Self-'review'")
			return

		review_web_url = review["_links"]["html"]["href"]
		if review["state"] == "commented":
			# Github sends webhooks for simple comments and treats them as "reviews".
			review_id = review_web_url.split(f"{pr['number']}#pullrequestreview-")
			assert len(review_id) == 2
			review_id = int(review_id[1])

			comment_list_url = f"https://api.github.com/repos/{repo_name}/pulls/{pr['number']}/comments"
			comment_list = requests.get(comment_list_url, headers=self.headers)
			comment_list.raise_for_status()
			actually_a_review = False
			for comment in comment_list.json():
				if comment["pull_request_review_id"] == review_id:
					if comment.get("in_reply_to_id") is None:
						actually_a_review = True
			if not actually_a_review:
				self.logger.info(f"Concluded that reivew id {review_id} is not a real review. It is theoretically at {review_web_url}")

		pr_record = PullRequest.query.filter_by(pr_number=pr['number'], repo_name=repo_name).first()

		if not pr_record:
			self.logger.error(f"No PR Record for: {pr['number']}")
			return

		requests_completed = Review.query.filter(
			Review.repo_name == repo_name,
			Review.pr_number == pr['number'],
			Review.completed_at == None,
			Review.reviewer == reviewer).all()

		for request in requests_completed:
			request.completed_at = datetime.utcnow()
			request.review_url = review_web_url

		if len(requests_completed) == 0:
			new_review = Review(repo_name=repo_name, pr_number=pr['number'], reviewer=reviewer, completed_at = datetime.utcnow(), review_url = review_web_url)
			self.db.session.add(new_review)
			self.db.session.commit()
			return

		# Update PR status to reviewed
		pr_record.status = PRStatus.REVIEWED
		self.db.session.commit()

		# Update the initial bot comment

		# After first review, ask if a second reviewer is needed
		self._ask_for_second_reviewer(pr, pr_record)

	def assign_reviewer(self, repo_name, pr_number, reviewer):
		"""Assign reviewers to a PR."""
		url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_number}/requested_reviewers"
		response = requests.post(url, headers=self.headers, json={'reviewers': [reviewer]})
		if response.status_code != 201:
			raise Exception(f"Failed to assign reviewers: {response.text}")
		self._add_pending_review(repo_name, pr_number, reviewer)

	def _create_comment(self, repo_url, pr_number, body):
		"""Create a comment on a PR."""
		comments_url = f"{repo_url}/issues/{pr_number}/comments"
		response = requests.post(comments_url,
									headers=self.headers,
									json={'body': body})
		if response.status_code != 201:
			self.logger.error(f"Failed to create comment: {response.text}")
			return None
		return response.json().get('id')

	def _update_comment(self, repo_url, pr_record, body):
		"""Update an existing comment."""
		if pr_record.initial_comment_id:
			comment_url = f"{repo_url}/issues/comments/{pr_record.initial_comment_id}"
			response = requests.patch(comment_url, headers=self.headers, json={'body': body})
			if response.status_code != 200:
				self.logger.error(f"Failed to update comment: {response.text}")
		else:
			self.create_comment(repo_url, pr_record.pr_number, body)

	def get_stats(self):
		"""Get bot statistics."""
		active_prs = PullRequest.query.filter(PullRequest.status != PRStatus.CLOSED).count()
		total_reviews = Review.query.count()

		return {'active_prs': active_prs, 'total_reviews': total_reviews}

	def get_repo_collaborators(self, repo_name):
		"""Get list of collaborators for a repository."""
		#url = f"https://api.github.com/repos/{repo_name}/collaborators"
		#response = requests.get(url, headers=self.headers)
		#if response.status_code != 200:
		#	self.logger.error(f"Failed to get collaborators: {response.text}")
		#	return []
		#return [user['login'] for user in response.json()]
		return ["arik-so", "jkczyz", "TheBlueMatt", "valentinewallace", "wpaulino", "joostjager"]

	def get_recent_reviews(self):
		"""Get count of open PRs assigned to each reviewer."""
		reviewer_counts = {}

		prs = PullRequest.query.all()

		recent_threshold = datetime.utcnow() - timedelta(days=7)
		for pr in prs:
			reviewer_set = set()
			reviews = Review.query.filter(
				Review.pr_number==pr.pr_number,
				Review.completed_at > recent_threshold
			).all()
			for review in reviews:
				reviewer_set.add(review.reviewer)

			for reviewer in reviewer_set:
				reviewer_counts[reviewer] = reviewer_counts.get(reviewer, 0) + 1

		return reviewer_counts

	def _auto_assign_next_reviewer(self, pr_record, pr):
		repo_name = pr['base']['repo']['full_name']
		pr_number = pr['number']
		pr_author = pr['user']['login']

		# Skip if PR is in draft
		if pr.get('draft', False):
			self.logger.info(f"PR #{pr_number} is in draft, skipping auto-assignment")
			return

		if pr.get('closed_at') is not None:
			self.logger.info(f"PR #{pr_number} is closed, skipping auto-assignment")
			return

		if pr.get('merged_at') is not None:
			self.logger.info(f"PR #{pr_number} is merged, skipping auto-assignment")
			return

		# Get collaborators excluding PR author
		collaborators = [
			c for c in self.get_repo_collaborators(repo_name)
			if c != pr_author
		]

		if not collaborators:
			self.logger.error(f"No eligible reviewers found for PR #{pr_number}")
			return

		current_reviewers = [
			user['login']
			for user in pr.get('requested_reviewers', [])
		]

		reviews = Review.query.filter_by(pr_number=pr_number, repo_name=repo_name).all()
		for review in reviews:
			current_reviewers.append(review.reviewer)

		reviewer_counts = self.get_recent_reviews()

		# Initialize counts for new collaborators
		for collaborator in collaborators:
			if collaborator not in reviewer_counts:
				reviewer_counts[collaborator] = 0

		# Sort collaborators by PR count
		sorted_reviewers = sorted(collaborators, key=lambda x: reviewer_counts.get(x, 0))

		self.logger.info(f"Possible reviewers for #{pr_number} and their review counts: {str(reviewer_counts)}")

		min_reviews = reviewer_counts[sorted_reviewers[0]]
		possible_reviewers = [reviewer for reviewer in sorted_reviewers if reviewer_counts[reviewer] == min_reviews]
		selected_reviewer = random.choice(possible_reviewers)

		if selected_reviewer is not None:
			self.assign_reviewer(repo_name, pr_number, selected_reviewer)

			if pr_record is not None:
				pr_record.status = PRStatus.PENDING_REVIEW
				self.db.session.commit()

		return selected_reviewer

	def auto_assign_reviewers(self, pr_record):
		"""Auto-assign reviewers to a PR based on workload."""
		try:
			# Get PR data including author and existing reviewers
			pr_url = f"https://api.github.com/repos/{pr_record.repo_name}/pulls/{pr_record.pr_number}"
			pr_response = requests.get(pr_url, headers=self.headers)
			pr_response.raise_for_status()
			pr_data = pr_response.json()

			# Check if PR already has reviewers assigned
			if pr_data.get('requested_reviewers') and len(pr_data['requested_reviewers']) > 0:
				self.logger.info(f"PR #{pr_record.pr_number} already has reviewers assigned, skipping auto-assignment")
				# Update PR status to needs_review since it already has reviewers
				pr_record.status = PRStatus.PENDING_REVIEW
				self.db.session.commit()
				return

			selected_reviewer = self._auto_assign_next_reviewer(pr_record, pr_data)

			if selected_reviewer is not None:
				# Update the initial comment
				comment = (
					f"I've assigned @{selected_reviewer} as a reviewer!\n"
					"I'll wait for their review and will help manage the review process.\n"
					"Once they submit their review, I'll check if a second reviewer would be helpful."
				)
				self._update_comment(pr_data['base']['repo']['url'], pr_record, comment)

				self.logger.info(f"Auto-assigned first reviewer for PR #{pr_record.pr_number}: {selected_reviewer}")
		except Exception as e:
			self.logger.exception(f"Error auto-assigning reviewers: {str(e)}")

	def check_and_send_reminders(self):
		"""Check for PRs needing review reminders and auto-assign reviewers."""
		self.logger.info("Checking for PRs needing review reminders...")

		current_time = datetime.utcnow()
		reviewer_threshold = current_time - timedelta(minutes=10)
		reminder_threshold = current_time - timedelta(days=2)

		# Force a new connection from the pool
		self.db.session.remove()

		# Find PRs needing reviewer assignment (no reviewers after 10 minutes)
		prs_needing_assignment = PullRequest.query.filter(
			PullRequest.status == PRStatus.PENDING_REVIEWER_CHOICE,
			PullRequest.created_at <= reviewer_threshold).all()

		# Auto-assign reviewers for these PRs
		for pr in prs_needing_assignment:
			self.auto_assign_reviewers(pr)

		# Nag reviewers, but only on weekdays
		now = datetime.utcnow()
		if now.weekday() < 4 or (now.weekday() == 5 and now.hour < 17):
			reviews_needing_reminders = Review.query.filter(
					Review.completed_at.is_(None),
					((Review.last_reminder_sent.is_(None) &
						(Review.requested_at <= reminder_threshold)) |
					(Review.last_reminder_sent <= reminder_threshold))
				).all()

			for review in reviews_needing_reminders:
				self._send_review_reminder(review)

	def _send_review_reminder(self, review):
		"""Send a reminder comment on a PR."""
		try:
			# Get assigned reviewers for the PR
			repo_url = f"https://api.github.com/repos/{review.repo_name}"
			pr_url = f"{repo_url}/pulls/{review.pr_number}"

			response = requests.get(pr_url, headers=self.headers)
			response.raise_for_status()

			pr_data = response.json()
			reviewers = [
				user['login']
				for user in pr_data.get('requested_reviewers', [])
			]

			if not reviewers:
				self.logger.info(f"No reviewers to remind for PR #{review.pr_number}")
				return

			# Create reminder message tagging all reviewers
			reviewer_tags = ' '.join(
				[f'@{reviewer}' for reviewer in reviewers])
			reminder_count = review.reminder_count + 1
			ordinal = lambda n: "%d%s" % (n, "tsnrhtdd"[
				(n // 10 % 10 != 1) * (n % 10 < 4) * n % 10::4])

			message = (
				f"🔔 {ordinal(reminder_count)} Reminder\n\n"
				f"Hey {reviewer_tags}! This PR has been waiting for your review.\n"
				"Please take a look when you have a chance. If you're unable to review, "
				"please let us know so we can find another reviewer.")

			# Post the reminder comment
			self._create_comment(repo_url, review.pr_number, message)

			# Update reminder tracking
			review.last_reminder_sent = datetime.utcnow()
			review.reminder_count = reminder_count
			self.db.session.commit()

			self.logger.info(f"Sent review reminder for PR #{review.pr_number}")

		except Exception as e:
			self.logger.exception(f"Error sending reminder for PR #{review.pr_number}: {str(e)}")

	def _has_bot_comment_about_second_reviewer(self, repo_url, pr_number):
		"""Check if bot has already asked about second reviewer."""
		# Get all comments on the PR
		comments_url = f"{repo_url}/issues/{pr_number}/comments"
		response = requests.get(comments_url, headers=self.headers)
		response.raise_for_status()

		comments = response.json()
		# Look for our specific second reviewer question
		for comment in comments:
			if "Do you think this PR is ready for a second reviewer?" in comment.get(
					'body', ''):
				return True
		return False

	def get_current_reviewers(self, repo_name, pr_number):
		repo_url = f"https://api.github.com/repos/{repo_name}"

		pr_url = f"{repo_url}/pulls/{pr_number}"
		response = requests.get(pr_url, headers=self.headers)
		response.raise_for_status()

		pr_data = response.json()
		current_reviewers = [
			user['login']
			for user in pr_data.get('requested_reviewers', [])
		]

		reviews = Review.query.filter_by(pr_number=pr_number, repo_name=repo_name).all()
		for review in reviews:
			current_reviewers.append(review.reviewer)

		return current_reviewers

	def _ask_for_second_reviewer(self, pr, pr_record):
		"""Ask if a second reviewer is needed after first review."""
		try:
			current_reviewers = self.get_current_reviewers(pr_record.repo_name, pr_record.pr_number)

			# If there's already more than one reviewer, don't ask
			if len(current_reviewers) > 1:
				return

			repo_url = f"https://api.github.com/repos/{pr_record.repo_name}"
			# Check if we've already asked about a second reviewer
			if self._has_bot_comment_about_second_reviewer(repo_url, pr_record.pr_number):
				self.logger.info(f"Already asked about second reviewer for PR #{pr_record.pr_number}")
				return

			# Create a comment asking if a second reviewer is needed
			second_reviewer_url = f"{APP_BASE_URL}/assign-second-reviewer/{pr_record.repo_name}/{pr_record.pr_number}"

			message = (
				"👋 The first review has been submitted!\n\n"
				"Do you think this PR is ready for a second reviewer? "
				f"If so, [click here to assign a second reviewer]({second_reviewer_url})."
			)

			self._create_comment(repo_url, pr_record.pr_number, message)
			self.logger.info(f"Asked about second reviewer for PR #{pr_record.pr_number}")

		except Exception as e:
			self.logger.exception(f"Error asking for second reviewer: {str(e)}")

	def assign_second_reviewer(self, repo_name, pr_number):
		"""Assign a second reviewer to a PR."""
		try:
			pr_record = PullRequest.query.filter_by(pr_number=pr_number, repo_name=repo_name).first()

			if not pr_record:
				self.logger.error(f"No PR record found for {repo_name}#{pr_number}")
				return False

			# Get PR data
			pr_url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_number}"
			response = requests.get(pr_url, headers=self.headers)
			response.raise_for_status()

			pr_data = response.json()
			pr_author = pr_data['user']['login']

			selected_reviewer = self._auto_assign_next_reviewer(pr_record, pr_data)
			if selected_reviewer:
				self.logger.info(f"Assigned second reviewer for PR #{pr_number}: {selected_reviewer}")

				# Post a comment
				repo_url = f"https://api.github.com/repos/{repo_name}"
				comment = f"✅ Added second reviewer: @{selected_reviewer}"
				self._create_comment(repo_url, pr_number, comment)

				return True
			return False
		except Exception as e:
			self.logger.exception(f"Error assigning second reviewer: {str(e)}")
			return False
