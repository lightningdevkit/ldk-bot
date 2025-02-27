from datetime import datetime
from db import db

class PullRequest(db.Model):
    __tablename__ = 'pull_request'

    id = db.Column(db.Integer, primary_key=True)
    pr_number = db.Column(db.Integer, nullable=False)
    repo_name = db.Column(db.String(200), nullable=False)
    title = db.Column(db.String(500))
    status = db.Column(db.String(50), default='pending_reviewer_choice')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_reminder_sent = db.Column(db.DateTime, nullable=True)
    reminder_count = db.Column(db.Integer, default=0)
    initial_comment_id = db.Column(db.BigInteger, nullable=True)  # Changed to BigInteger to handle large GitHub comment IDs
    reviews = db.relationship('Review', backref='pull_request', lazy=True)

class Review(db.Model):
    __tablename__ = 'review'

    id = db.Column(db.Integer, primary_key=True)
    pr_id = db.Column(db.Integer, db.ForeignKey('pull_request.id'), nullable=False)
    reviewer = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)