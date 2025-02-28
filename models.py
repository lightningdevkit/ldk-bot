from datetime import datetime
from db import db
from enum import Enum

class PRStatus(Enum):
    PENDING_REVIEWER_CHOICE = 0
    DRAFT = 1
    PENDING_REVIEW = 2
    CLOSED = 3

class PullRequest(db.Model):
    __tablename__ = 'pull_request'

    pr_number = db.Column(db.Integer, primary_key=True)
    repo_name = db.Column(db.String(200), nullable=False)
    status = db.Column(db.Enum(PRStatus), default=PRStatus.PENDING_REVIEWER_CHOICE)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_reminder_sent = db.Column(db.DateTime, nullable=True)
    reminder_count = db.Column(db.Integer, default=0)
    initial_comment_id = db.Column(db.BigInteger, nullable=True)
    reviews = db.relationship('Review', backref='pr_id', lazy=True)

class Review(db.Model):
    __tablename__ = 'review'

    id = db.Column(db.Integer, primary_key=True)
    pr_number = db.Column(db.Integer, db.ForeignKey('pull_request.pr_number'), nullable=False)
    reviewer = db.Column(db.String(100), nullable=False)
    requested_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    @property
    def pending_duration(self):
        """Get current pending duration in minutes for incomplete reviews"""
        if not self.completed_at:
            delta = datetime.utcnow() - self.requested_at
            return int(delta.total_seconds() / 60)
        return None