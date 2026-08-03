from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import JobStatus, PlatformAccount, VideoJob
from backend.routers import jobs as jobs_router


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


class PublishJobTests(unittest.TestCase):
    def test_happy_path_dispatches_all_pending_platforms(self) -> None:
        db = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal Teste", niche="geral")
        db.add(account)
        db.flush()

        job = VideoJob(
            title="Video pronto",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.APPROVED,
            target_platforms=["youtube", "tiktok"],
            publish_status={},
        )
        db.add(job)
        db.commit()
        job_id = job.id

        with patch("backend.routers.jobs.dispatch_publish", return_value="celery") as mock_dispatch:
            result = jobs_router.publish_job(job_id, db=db)

        mock_dispatch.assert_called_once_with(job_id)
        self.assertEqual(result["publish_dispatch"], "celery")
        self.assertCountEqual(result["pending_platforms"], ["youtube", "tiktok"])
        self.assertEqual(result["job"]["status"], JobStatus.APPROVED.value)

    def test_mixed_published_and_pending_platforms_only_reports_pending(self) -> None:
        db = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal Teste", niche="geral")
        db.add(account)
        db.flush()

        job = VideoJob(
            title="Video parcialmente publicado",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.ERROR,
            target_platforms=["youtube", "tiktok"],
            publish_status={"youtube": {"ok": True, "status": "ok"}},
        )
        db.add(job)
        db.commit()
        job_id = job.id

        with patch("backend.routers.jobs.dispatch_publish", return_value="celery") as mock_dispatch:
            result = jobs_router.publish_job(job_id, db=db)

        mock_dispatch.assert_called_once_with(job_id)
        self.assertEqual(result["pending_platforms"], ["tiktok"])
        db.refresh(job)
        self.assertEqual(job.status, JobStatus.APPROVED)
        self.assertEqual(job.approval_status, "approved")

    def test_fully_published_job_is_a_no_op_and_flips_to_published(self) -> None:
        db = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal Teste", niche="geral")
        db.add(account)
        db.flush()

        job = VideoJob(
            title="Video totalmente publicado",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.APPROVED,
            target_platforms=["youtube"],
            publish_status={"youtube": {"ok": True, "status": "ok"}},
        )
        db.add(job)
        db.commit()
        job_id = job.id

        with patch("backend.routers.jobs.dispatch_publish") as mock_dispatch:
            result = jobs_router.publish_job(job_id, db=db)

        mock_dispatch.assert_not_called()
        self.assertIsNone(result["publish_dispatch"])
        self.assertEqual(result["pending_platforms"], [])
        db.refresh(job)
        self.assertEqual(job.status, JobStatus.PUBLISHED)

    def test_invalid_status_returns_409(self) -> None:
        db = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal Teste", niche="geral")
        db.add(account)
        db.flush()

        job = VideoJob(
            title="Video em fila",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.QUEUED,
            target_platforms=["youtube"],
        )
        db.add(job)
        db.commit()
        job_id = job.id

        with patch("backend.routers.jobs.dispatch_publish") as mock_dispatch:
            with self.assertRaises(HTTPException) as ctx:
                jobs_router.publish_job(job_id, db=db)

        self.assertEqual(ctx.exception.status_code, 409)
        mock_dispatch.assert_not_called()

    def test_job_not_found_returns_404(self) -> None:
        db = _make_session()
        with self.assertRaises(HTTPException) as ctx:
            jobs_router.publish_job(999999, db=db)
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
