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


def _make_job(db, status):
    account = PlatformAccount(platform="youtube", display_name="Canal Teste", niche="geral")
    db.add(account)
    db.flush()
    job = VideoJob(
        title="Video pendente",
        mode="from_title",
        content_type="film_recap_ai_images",
        video_format="long",
        account_id=account.id,
        status=status,
    )
    db.add(job)
    db.commit()
    return job


class ApproveRejectTransitionTests(unittest.TestCase):
    def test_approve_from_awaiting_approval_dispatches_publish(self) -> None:
        db = _make_session()
        job = _make_job(db, JobStatus.AWAITING_APPROVAL)

        with patch("backend.routers.jobs.dispatch_publish", return_value={"ok": True}) as mock_dispatch:
            result = jobs_router.approve_job(job.id, db=db)

        self.assertEqual(job.status, JobStatus.APPROVED)
        self.assertEqual(job.approval_status, "approved")
        self.assertEqual(result["job"]["status"], JobStatus.APPROVED.value)
        mock_dispatch.assert_called_once_with(job.id)

    def test_approve_rejects_job_not_awaiting_approval(self) -> None:
        db = _make_session()
        job = _make_job(db, JobStatus.PROCESSING)

        with self.assertRaises(HTTPException) as ctx:
            jobs_router.approve_job(job.id, db=db)

        self.assertEqual(ctx.exception.status_code, 409)
        # Status must remain untouched — no silent approval of an in-flight job.
        self.assertEqual(job.status, JobStatus.PROCESSING)

    def test_reject_from_awaiting_approval_sets_rejected(self) -> None:
        db = _make_session()
        job = _make_job(db, JobStatus.AWAITING_APPROVAL)

        result = jobs_router.reject_job(job.id, db=db)

        self.assertEqual(job.status, JobStatus.REJECTED)
        self.assertEqual(job.approval_status, "rejected")
        self.assertEqual(result["job"]["status"], JobStatus.REJECTED.value)

    def test_reject_rejects_job_not_awaiting_approval(self) -> None:
        db = _make_session()
        job = _make_job(db, JobStatus.PUBLISHED)

        with self.assertRaises(HTTPException) as ctx:
            jobs_router.reject_job(job.id, db=db)

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(job.status, JobStatus.PUBLISHED)


if __name__ == "__main__":
    unittest.main()
