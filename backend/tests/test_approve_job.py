from __future__ import annotations

import importlib.util
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


class ApproveJobDispatchFailureTests(unittest.TestCase):
    def test_real_dispatch_publish_error_is_surfaced_not_hidden(self) -> None:
        db = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal Teste", niche="geral")
        db.add(account)
        db.flush()

        job = VideoJob(
            title="Video pendente",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.AWAITING_APPROVAL,
        )
        db.add(job)
        db.commit()

        with patch.object(importlib.util, "find_spec", return_value=True), \
             patch("backend.routers.jobs.dispatch_publish", side_effect=RuntimeError("credencial inválida")):
            result = jobs_router.approve_job(job.id, db=db)

        # The job must still end up approved...
        self.assertEqual(result["job"]["status"], JobStatus.APPROVED.value)
        # ...but the real error must be surfaced, not masked as "publisher not configured".
        self.assertIsNone(result["publish_dispatch"])
        self.assertIn("credencial inválida", result["note"])
        self.assertNotIn("ainda não configurado", result["note"])
        self.assertIsNotNone(job.error_message)
        self.assertIn("credencial inválida", job.error_message)

    def test_unexpected_dispatch_publish_failure_is_logged(self) -> None:
        db = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal Teste", niche="geral")
        db.add(account)
        db.flush()

        job = VideoJob(
            title="Video pendente",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.AWAITING_APPROVAL,
        )
        db.add(job)
        db.commit()
        job_id = job.id

        with patch.object(importlib.util, "find_spec", return_value=True), \
             patch("backend.routers.jobs.dispatch_publish", side_effect=RuntimeError("bug inesperado")), \
             patch.object(jobs_router.logger, "exception") as mock_log:
            jobs_router.approve_job(job_id, db=db)

        mock_log.assert_called_once()
        self.assertIn("dispatch_publish", mock_log.call_args[0][0])
        self.assertEqual(mock_log.call_args[0][1], job_id)


if __name__ == "__main__":
    unittest.main()
