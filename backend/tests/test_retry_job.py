from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import JobStatus, PlatformAccount, VideoJob
from backend.routers import jobs as jobs_router
from fastapi import HTTPException


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


class RetryJobTests(unittest.TestCase):
    def test_retry_resets_job_and_dispatches(self) -> None:
        db = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal Teste", niche="geral")
        db.add(account)
        db.flush()

        job = VideoJob(
            title="Video com erro",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.ERROR,
            error_message="falha anterior",
            progress=42,
            retry_count=1,
        )
        db.add(job)
        db.commit()

        with patch("backend.routers.jobs.dispatch_job", return_value="celery") as mock_dispatch:
            result = jobs_router.retry_job(job.id, db=db)

        self.assertEqual(result["job"]["status"], JobStatus.QUEUED.value)
        self.assertEqual(job.status, JobStatus.QUEUED)
        self.assertIsNone(job.error_message)
        self.assertEqual(job.progress, 0)
        self.assertEqual(job.retry_count, 2)
        mock_dispatch.assert_called_once_with(job.id)

    def test_retry_blocks_job_in_running_state(self) -> None:
        db = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal Teste", niche="geral")
        db.add(account)
        db.flush()

        job = VideoJob(
            title="Video em execução",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.PROCESSING,
        )
        db.add(job)
        db.commit()

        with patch("backend.routers.jobs.dispatch_job") as mock_dispatch:
            with self.assertRaises(HTTPException) as ctx:
                jobs_router.retry_job(job.id, db=db)

        self.assertEqual(ctx.exception.status_code, 409)
        mock_dispatch.assert_not_called()
        # Status/fields must remain untouched by the blocked retry attempt.
        self.assertEqual(job.status, JobStatus.PROCESSING)


if __name__ == "__main__":
    unittest.main()
