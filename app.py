import os
import logging
from flask import Flask, request, render_template, jsonify
from dotenv import load_dotenv
from db import db
from github_bot import GitHubBot
from datetime import datetime
import threading
import time

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET")

# Configure database
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}
db.init_app(app)

# Initialize GitHub bot
github_bot = GitHubBot(
    token=os.environ.get("GITHUB_TOKEN"),
    webhook_secret=os.environ.get("WEBHOOK_SECRET"),
    db=db
)

def reminder_scheduler():
    """Background thread to periodically check and send reminders."""
    with app.app_context():
        while True:
            try:
                github_bot.check_and_send_reminders()
            except Exception as e:
                logger.error(f"Error in reminder scheduler: {str(e)}")
            # Sleep for 1 hour before next check
            time.sleep(3600)

# Start the reminder scheduler thread
reminder_thread = threading.Thread(target=reminder_scheduler, daemon=True)
reminder_thread.start()

with app.app_context():
    # Import models here to avoid circular imports
    import models  # noqa: F401
    db.create_all()

@app.route('/')
def index():
    """Render the dashboard page."""
    return render_template('index.html')

@app.route('/choose-reviewers/<repo_name>/<int:pr_number>')
def choose_reviewers(repo_name, pr_number):
    """Render the reviewer selection page."""
    return render_template('choose_reviewers.html', repo_name=repo_name, pr_number=pr_number)

@app.route('/submit-reviewers', methods=['POST'])
def submit_reviewers():
    """Handle reviewer submission."""
    pr_number = request.form.get('pr_number')
    repo_name = request.form.get('repo_name')
    reviewers = [r.strip() for r in request.form.get('reviewers', '').split(',') if r.strip()]
    
    try:
        github_bot.assign_reviewers(repo_name, pr_number, reviewers)
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle GitHub webhook events."""
    # Verify webhook signature
    signature = request.headers.get('X-Hub-Signature-256')
    if not github_bot.verify_webhook(signature, request.data):
        return jsonify({'error': 'Invalid signature'}), 401

    event = request.headers.get('X-GitHub-Event')
    data = request.json

    try:
        if event == 'pull_request':
            github_bot.handle_pr_event(data)
        elif event == 'pull_request_review':
            github_bot.handle_review_event(data)
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
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
        logger.error(f"Error checking reminders: {str(e)}")
        return jsonify({'error': str(e)}), 500