"""Regression test: GET /analytics/videos must not do N+1 queries for job.analytics."""
from __future__ import annotations

import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import backend.models  # noqa: F401  (registers all mappers on Base.metadata)
from backend.database import Base
from backend.models import VideoAnalytics, VideoJob
from backend.routers.analytics import videos as videos_endpoint


class AnalyticsVideosQueryCountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def tearDown(self) -> None:
        Base.metadata.drop_all(bind=self.engine)

    def test_videos_endpoint_does_not_n_plus_one_query_analytics(self) -> None:
        db = self.SessionLocal()
        try:
            for i in range(10):
                job = VideoJob(
                    title=f"Video {i}",
                    publish_status={"youtube": {"url": f"https://youtu.be/{i}", "video_id": str(i)}},
                )
                db.add(job)
                db.flush()
                db.add(VideoAnalytics(job_id=job.id, platform="youtube", views=i))
            db.commit()

            query_count = 0

            def _count(*args, **kwargs):
                nonlocal query_count
                query_count += 1

            event.listen(self.engine, "before_cursor_execute", _count)
            try:
                result = videos_endpoint(account_id=None, limit=200, db=db)
            finally:
                event.remove(self.engine, "before_cursor_execute", _count)

            self.assertEqual(result["count"], 10)
            # One query for the jobs + one (batched) selectinload query for analytics.
            # Without eager loading this would be 1 + 10 (one per job).
            self.assertLessEqual(query_count, 3)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
