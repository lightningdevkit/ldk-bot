import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from flask import Flask

from db import db
from github_bot import GitHubBot
from models import PRStatus, PullRequest, Review


class ReviewReminderTests(unittest.TestCase):
	def setUp(self):
		self.app = Flask(__name__)
		self.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
		self.app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
		db.init_app(self.app)
		self.app_context = self.app.app_context()
		self.app_context.push()
		db.create_all()

	def tearDown(self):
		db.session.remove()
		db.drop_all()
		self.app_context.pop()

	def test_overdue_review_does_not_post_reminder_comment(self):
		now = datetime(2026, 6, 23, 12, 0, 0)
		pr = PullRequest(
			repo_name="lightningdevkit/ldk-node",
			pr_number=9999,
			pr_title="Drop reminder comments",
			status=PRStatus.PENDING_REVIEW,
			created_at=now - timedelta(days=3),
		)
		review = Review(
			repo_name=pr.repo_name,
			pr_number=pr.pr_number,
			reviewer="tnull",
			requested_at=now - timedelta(days=3),
		)
		db.session.add_all([pr, review])
		db.session.commit()

		bot = GitHubBot("token", "secret", db)
		pr_response = Mock()
		pr_response.raise_for_status.return_value = None
		pr_response.json.return_value = {"requested_reviewers": [{"login": review.reviewer}]}

		with patch("github_bot.datetime") as datetime_mock, \
				patch("github_bot.requests.get", return_value=pr_response), \
				patch.object(bot, "_create_comment") as create_comment:
			datetime_mock.utcnow.return_value = now

			bot.check_and_auto_assign_reviewers()

		create_comment.assert_not_called()

	def test_unassigned_prs_are_still_auto_assigned_after_grace_period(self):
		now = datetime(2026, 6, 23, 12, 0, 0)
		pr_number = 10000
		pr = PullRequest(
			repo_name="lightningdevkit/ldk-node",
			pr_number=pr_number,
			pr_title="Needs a reviewer",
			status=PRStatus.PENDING_REVIEWER_CHOICE,
			created_at=now - timedelta(minutes=11),
		)
		db.session.add(pr)
		db.session.commit()

		bot = GitHubBot("token", "secret", db)

		with patch("github_bot.datetime") as datetime_mock, \
				patch.object(bot, "auto_assign_reviewers") as auto_assign_reviewers:
			datetime_mock.utcnow.return_value = now

			bot.check_and_auto_assign_reviewers()

		auto_assign_reviewers.assert_called_once()
		self.assertEqual(auto_assign_reviewers.call_args.args[0].pr_number, pr_number)


if __name__ == "__main__":
	unittest.main()
