import os
import logging
from flask import Flask, request, render_template, jsonify
from github_bot import GitHubBot
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET")

# Initialize GitHub bot
github_bot = GitHubBot(
    token=os.environ.get("GITHUB_TOKEN"),
    webhook_secret=os.environ.get("WEBHOOK_SECRET")
)

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
