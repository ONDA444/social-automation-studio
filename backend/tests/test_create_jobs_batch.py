from __future__ import annotations

import unittest
from unittest.mock import patch

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


class CreateJobsBatchTests(unittest.TestCase):
    def test_creates_one_job_per_theme_and_gates_only_the_first_for_a_new_channel(self) -> None:
        db = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal Novo", niche="geral")
        db.add(account)
        db.commit()

        payload = jobs_router.JobBatchCreate(
            themes=["Tema Um", "Tema Dois", "Tema Tres"],
            content_type="film_recap_ai_images",
            format="long",
            target_platforms=["youtube"],
            account_id=account.id,
        )

        with patch("backend.routers.jobs.dispatch_job", return_value="celery") as mock_dispatch:
            result = jobs_router.create_jobs_batch(payload, db=db)

        self.assertEqual(result["created"], 3)
        self.assertEqual(len(result["job_ids"]), 3)
        self.assertEqual(mock_dispatch.call_count, 3)

        jobs = db.query(VideoJob).order_by(VideoJob.id.asc()).all()
        self.assertEqual([j.status for j in jobs], [JobStatus.QUEUED] * 3)
        self.assertTrue(jobs[0].video_context.get("require_approval"))
        self.assertFalse(jobs[1].video_context.get("require_approval"))
        self.assertFalse(jobs[2].video_context.get("require_approval"))

    def test_batch_insert_uses_a_single_commit_not_one_per_theme(self) -> None:
        db = _make_session()
        payload = jobs_router.JobBatchCreate(
            themes=["Um", "Dois", "Tres", "Quatro"],
            content_type="film_recap_ai_images",
            format="long",
            target_platforms=["youtube"],
            account_id=None,
        )

        with patch("backend.routers.jobs.dispatch_job", return_value="celery"), \
             patch.object(db, "commit", wraps=db.commit) as mock_commit:
            jobs_router.create_jobs_batch(payload, db=db)

        self.assertEqual(mock_commit.call_count, 1)

    def test_invalid_content_type_returns_400(self) -> None:
        from fastapi import HTTPException

        db = _make_session()
        payload = jobs_router.JobBatchCreate(themes=["Tema"], content_type="tipo_invalido")
        with self.assertRaises(HTTPException) as ctx:
            jobs_router.create_jobs_batch(payload, db=db)
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
