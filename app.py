import os
import logging
import threading
import time
from datetime import datetime
from flask import Flask, request, render_template, jsonify

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET")

# Initialize database
from db import init_db
init_db(app)

logger.info("init'd db...")

with app.app_context():
	# Import models here to avoid circular imports
	from models import PullRequest, Review, PRStatus  # noqa: F401
	try:
		from db import db
		db.create_all()
		logger.info("Successfully created database tables")
	except Exception as e:
		logger.error(f"Error creating database tables: {str(e)}")
		raise

	# Initialize GitHub bot
	from github_bot import GitHubBot
	github_bot = GitHubBot(
		token=os.environ.get("GITHUB_TOKEN"),
		webhook_secret=os.environ.get("WEBHOOK_SECRET"),
		db=db
	)
	logger.info("init'd github bot...")

	github_bot.sync_existing_prs()

def reminder_scheduler():
	"""Background thread to periodically check and send reminders."""
	with app.app_context():
		while True:
			try:
				github_bot.check_and_send_reminders()
			except Exception as e:
				logger.exception(f"Error in reminder scheduler: {str(e)}")
			# Sleep for 1 minute before next check
			time.sleep(60)

# Start the reminder scheduler thread
reminder_thread = threading.Thread(target=reminder_scheduler, daemon=True)
reminder_thread.start()


@app.route('/')
def index():
	"""Render the dashboard page."""
	return render_template('index.html')

@app.route('/webhook', methods=['POST'])
def webhook():
	"""Handle GitHub webhook events."""
	# Verify webhook signature
	signature = request.headers.get('X-Hub-Signature-256')
	if not github_bot.verify_webhook(signature, request.data):
		return jsonify({'error': 'Invalid signature'}), 401

	event = request.headers.get('X-GitHub-Event')
	data = request.json
	logger.info(f"Received {event} webhook event for a {data['action']}")

	try:
		if event == 'pull_request':
			github_bot.handle_pr_event(data)
		elif event == 'pull_request_review':
			github_bot.handle_review_event(data)
		return jsonify({'status': 'success'}), 200
	except Exception as e:
		logger.exception(f"Error processing webhook: {str(e)}")
		return jsonify({'error': str(e)}), 500

@app.route('/stats')
def stats():
	"""Return bot statistics."""
	return jsonify(github_bot.get_stats())

@app.route('/check-reminders', methods=['POST'])
def check_reminders():
	"""Manually trigger reminder checks."""
	try:
		github_bot.check_and_send_reminders()
		return jsonify({'status': 'success', 'message': 'Reminder check triggered'}), 200
	except Exception as e:
		logger.exception(f"Error checking reminders: {str(e)}")
		return jsonify({'error': str(e)}), 500

@app.route('/assign-second-reviewer/<repo_org>/<repo_name>/<int:pr_number>')
def assign_second_reviewer(repo_org, repo_name, pr_number):
	"""Assign a second reviewer to the PR."""
	try:
		success = github_bot.assign_second_reviewer(repo_org + "/" + repo_name, pr_number)
		if success:
			return render_template('success.html', 
								message="Second reviewer assigned successfully!",
								back_url=f"/")
		else:
			return render_template('error.html', 
								message="Failed to assign second reviewer.",
								back_url=f"/")
	except Exception as e:
		logger.exception(f"Error assigning second reviewer: {str(e)}")
		return render_template('error.html', 
							message=f"Error: {str(e)}",
							back_url=f"/")

@app.route('/reviewer-dashboard')
def reviewer_dashboard():
	"""Show reviewer statistics and pending reviews."""
	reviewers = {}

	# Get all reviews
	reviews = Review.query.order_by(Review.requested_at.desc()).all()

	for review in reviews:
		reviewer = review.reviewer
		if reviewer not in reviewers:
			reviewers[reviewer] = {
				'pending_reviews': [],
				'completed_reviews': [],
				'avg_duration': 0,
				'total_reviews': 0
			}

		if review.completed_at:
			reviewers[reviewer]['completed_reviews'].append(review)
			delta = review.completed_at - review.requested_at
			review_duration = int(delta.total_seconds() / 60)  # Duration in minutes
			reviewers[reviewer]['total_reviews'] += 1
			# Recalculate average duration
			current_total = reviewers[reviewer]['avg_duration'] * (reviewers[reviewer]['total_reviews'] - 1)
			new_avg = (current_total + review_duration) / reviewers[reviewer]['total_reviews']
			reviewers[reviewer]['avg_duration'] = round(new_avg, 1)
		else:
			reviewers[reviewer]['pending_reviews'].append(review)

	return render_template('reviewer_dashboard.html', reviewers=reviewers)
