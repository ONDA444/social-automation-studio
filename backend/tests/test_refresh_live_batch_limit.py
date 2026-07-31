"""Regression test: _job_refresh_live used to process EVERY published job on
every 8-minute tick, with no limit. Each iteration builds fresh Google API
client objects (cache_discovery=False, no reuse) and fires 2+ HTTP calls
(a near-guaranteed 401-then-refresh, doubled again for videos >=24h old via
with_analytics). Confirmed in production: as the published-job count grew
into the hundreds, this scaled into a burst of 1000+ HTTP calls/objects in a
single synchronous tick, and the web service was OOM-killed by the platform
mid-burst (a clean "Stopping Container" with zero app-level exception -- the
signature of an external SIGKILL, not a crash). The fix caps the batch and
rotates by least-recently-refreshed so coverage is spread across ticks
instead of one unbounded burst."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import scheduler
from backend.database import Base
from backend.models import JobStatus, PlatformAccount, VideoAnalytics, VideoJob


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


class RefreshLiveBatchLimitTests(unittest.TestCase):
    def _account(self, db):
        account = PlatformAccount(platform="youtube", display_name="Canal", niche="geral")
        db.add(account)
        db.flush()
        return account

    def _published_job(self, db, account, *, title):
        job = VideoJob(
            title=title, mode="from_ready_video", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status=JobStatus.PUBLISHED,
            publish_status={"youtube": {"ok": True, "video_id": title}},
        )
        db.add(job)
        db.flush()
        return job

    def test_batch_is_capped_even_with_hundreds_of_published_jobs(self) -> None:
        db, Session = _make_session(), None
        account = self._account(db)
        total = scheduler._REFRESH_LIVE_BATCH_LIMIT + 25
        for i in range(total):
            self._published_job(db, account, title=f"Video {i}")
        db.commit()

        with patch.object(scheduler, "SessionLocal", lambda: db), \
             patch("backend.agents.analytics.AnalyticsAgent.refresh_live", return_value=[{"views": 1}]) as fake_refresh, \
             patch("backend.events.publish_event"):
            scheduler._job_refresh_live()

        self.assertEqual(fake_refresh.call_count, scheduler._REFRESH_LIVE_BATCH_LIMIT)

    def test_never_refreshed_jobs_are_prioritized_over_recently_refreshed_ones(self) -> None:
        """Least-recently-refreshed-first ordering: a job whose 'live' snapshot
        was just collected must not crowd out one that was never refreshed
        (or refreshed a long time ago) once the batch is full."""
        db = _make_session()
        account = self._account(db)

        stale = self._published_job(db, account, title="Stale")
        db.add(VideoAnalytics(
            job_id=stale.id, platform="youtube", platform_video_id="Stale",
            snapshot_type="live", collected_at=datetime.utcnow() - timedelta(days=1),
        ))
        never_refreshed = self._published_job(db, account, title="NeverRefreshed")
        fresh = self._published_job(db, account, title="Fresh")
        db.add(VideoAnalytics(
            job_id=fresh.id, platform="youtube", platform_video_id="Fresh",
            snapshot_type="live", collected_at=datetime.utcnow(),
        ))
        db.commit()
        # _job_refresh_live() closes the session in its finally block, so
        # grab the ids now -- accessing them after the call would hit an
        # expired/detached instance.
        stale_id, never_refreshed_id, fresh_id = stale.id, never_refreshed.id, fresh.id

        seen_ids = []

        def fake_refresh(self_agent, job_id):
            seen_ids.append(job_id)
            return [{"views": 1}]

        with patch.object(scheduler, "SessionLocal", lambda: db), \
             patch.object(scheduler, "_REFRESH_LIVE_BATCH_LIMIT", 2), \
             patch("backend.agents.analytics.AnalyticsAgent.refresh_live", fake_refresh), \
             patch("backend.events.publish_event"):
            scheduler._job_refresh_live()

        self.assertEqual(len(seen_ids), 2)
        self.assertIn(never_refreshed_id, seen_ids)
        self.assertIn(stale_id, seen_ids)
        self.assertNotIn(fresh_id, seen_ids)


if __name__ == "__main__":
    unittest.main()
