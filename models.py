from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from enum import Enum
from db import db
import logging

logger = logging.getLogger("model")

def sub_times(end, start):
	assert end >= start
	start_dt = datetime(2020, 1, 1, hour=start.hour, minute=start.minute, second=start.second, microsecond=start.microsecond)
	end_dt = datetime(2020, 1, 1, hour=end.hour, minute=end.minute, second=end.second, microsecond=end.microsecond)
	return end_dt - start_dt

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
	pr_title = db.Column(db.String(500), nullable=False)
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
	review_url = db.Column(db.String(200), nullable=True)
	__table_args__ = (db.ForeignKeyConstraint([repo_name, pr_number],
						[PullRequest.repo_name, PullRequest.pr_number]), {})

	@property
	def pr_title(self):
		return PullRequest.query.filter_by(pr_number = self.pr_number, repo_name = self.repo_name).first().pr_title

	@property
	def review_duration(self):
		end = self.completed_at if self.completed_at else datetime.utcnow()
		end = end.replace(tzinfo=timezone.utc)
		start = self.requested_at.replace(tzinfo=timezone.utc)
		reviewer_tzs = {
			'TheBlueMatt': 'America/New_York',
			'valentinewallace': 'America/New_York',
			'wpaulino': 'America/Los_Angeles',
			'tnull': 'Europe/Berlin',
			'joostjager': 'Europe/Berlin',
			'jkczyz': 'America/Chicago',
			'arik-so': 'America/Los_Angeles'
		}
		reviewer_tz = reviewer_tzs.get(self.reviewer)
		if reviewer_tz is None:
			logger.warn(f"Missing timezone for reviewer {self.reviewer}")
			reviewer_tz = 'America/New_York'
		tzinfo = ZoneInfo(reviewer_tz)

		# Just do the naive calculation by looping
		start_localized = start.astimezone(tzinfo)
		end_localized = end.astimezone(tzinfo)
		total_time = timedelta(0)
		while start_localized < end_localized:
			workday_end = time(hour=17, minute=0, second=0, microsecond=0, tzinfo=tzinfo)
			if start_localized.weekday() > 4 or start_localized.hour < 9 or start_localized.timetz() > workday_end:
				if start_localized.weekday() > 4 or start_localized.timetz() > workday_end:
					start_localized += timedelta(days=1)
				start_localized = start_localized.replace(hour=9, minute=0, second=0, microsecond=0)
				continue
			workday_time = sub_times(workday_end, start_localized.timetz())
			if start_localized.date() == end_localized.date():
				actual_time = sub_times(end_localized.timetz(), start_localized.timetz())
				total_time += min(workday_time, actual_time)
				break
			else:
				total_time += workday_time
				start_localized = start_localized.replace(hour = 18) # Let the next loop iteration jump to the next day
		return total_time

	@property
	def review_duration_hours(self):
		return round(self.review_duration.total_seconds() / 3600)
