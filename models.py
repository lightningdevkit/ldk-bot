from datetime import datetime
from enum import Enum
from db import db

class PRStatus(Enum):
	PENDING_REVIEWER_CHOICE = 0
	DRAFT = 1
	PENDING_REVIEW = 2
	REVIEWED = 3
	CLOSED = 4

class PullRequest(db.Model):
	__tablename__ = 'pull_request'

	pr_number = db.Column(db.Integer, primary_key=True)
	repo_name = db.Column(db.String(200), primary_key=True)
	status = db.Column(db.Enum(PRStatus), default=PRStatus.PENDING_REVIEWER_CHOICE)
	created_at = db.Column(db.DateTime, default=datetime.utcnow)
	last_reminder_sent = db.Column(db.DateTime, nullable=True)
	reminder_count = db.Column(db.Integer, default=0)
	initial_comment_id = db.Column(db.BigInteger, nullable=True)

class Review(db.Model):
	__tablename__ = 'review'

	id = db.Column(db.Integer, primary_key=True)
	repo_name = db.Column(db.String(200), nullable=False)
	pr_number = db.Column(db.Integer, nullable=False)
	reviewer = db.Column(db.String(100), nullable=False)
	requested_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
	completed_at = db.Column(db.DateTime, nullable=True)
	__table_args__ = (db.ForeignKeyConstraint([repo_name, pr_number],
						[PullRequest.repo_name, PullRequest.pr_number]), {})

	@property
	def review_duration(self):
		if self.completed_at:
			delta = self.completed_at - self.requested_at
			return int(delta.total_seconds() / 60)
		return None

	@property
	def pending_duration(self):
		if not self.completed_at:
			delta = datetime.utcnow() - self.requested_at
			return int(delta.total_seconds() / 60)
		return None
