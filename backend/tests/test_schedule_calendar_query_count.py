"""Regression test: GET /schedule/calendar must not do N+1 queries for job.account."""
from __future__ import annotations

import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import backend.models  # noqa: F401  (registers all mappers on Base.metadata)
from backend.database import Base
from backend.models import PlatformAccount, VideoJob
from backend.routers.schedule import calendar as calendar_endpoint


class ScheduleCalendarQueryCountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def tearDown(self) -> None:
        Base.metadata.drop_all(bind=self.engine)

    def test_calendar_endpoint_does_not_n_plus_one_query_account(self) -> None:
        from datetime import datetime, timedelta

        db = self.SessionLocal()
        try:
            accounts = []
            for i in range(3):
                account = PlatformAccount(platform="youtube", display_name=f"Channel {i}")
                db.add(account)
                db.flush()
                accounts.append(account)

            base = datetime(2026, 7, 1, 12, 0)
            for i in range(10):
                job = VideoJob(
                    title=f"Video {i}",
                    account_id=accounts[i % len(accounts)].id,
                    scheduled_at=base + timedelta(hours=i),
                )
                db.add(job)
            db.commit()

            query_count = 0

            def _count(*args, **kwargs):
                nonlocal query_count
                query_count += 1

            event.listen(self.engine, "before_cursor_execute", _count)
            try:
                result = calendar_endpoint(db=db)
            finally:
                event.remove(self.engine, "before_cursor_execute", _count)

            self.assertEqual(len(result["events"]), 10)
            self.assertEqual(
                {e["channel_name"] for e in result["events"]},
                {"Channel 0", "Channel 1", "Channel 2"},
            )
            # One query for the jobs + one (batched) selectinload query for accounts
            # (plus a small constant for SQLite transaction bookkeeping).
            # Without eager loading this would be 1 + 10 (one per job).
            self.assertLessEqual(query_count, 5)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
