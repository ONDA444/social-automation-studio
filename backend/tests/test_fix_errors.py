from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
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


class FixErrorsTests(unittest.TestCase):
    """The Config page's one-click 'Corrigir erros' button must requeue and
    resend every ERROR job immediately, EXCEPT the handful where forcing a
    retry risks a duplicate upload or repeats a crash — those stay parked for
    a human, with a plain-Portuguese reason returned to the frontend."""

    def test_requeues_recoverable_errors_and_skips_duplicate_risk_ones(self) -> None:
        db = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal Teste", niche="geral")
        db.add(account)
        db.flush()

        dead_token = VideoJob(
            title="Token expirado",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.ERROR,
            error_message="invalid_grant: Token has been expired or revoked.",
        )
        upload_limit = VideoJob(
            title="Limite do YouTube",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.ERROR,
            error_message="HttpError 400 ... 'The user has exceeded the number of videos they may upload.'",
        )
        maybe_duplicate = VideoJob(
            title="Upload interrompido",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.ERROR,
            error_message="Falha ao publicar: PODE já estar no canal. Verifique o YouTube manualmente.",
        )
        still_processing = VideoJob(
            title="Em andamento",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.PROCESSING,
        )
        db.add_all([dead_token, upload_limit, maybe_duplicate, still_processing])
        db.commit()
        dead_id, limit_id, dup_id = dead_token.id, upload_limit.id, maybe_duplicate.id

        with patch("backend.routers.jobs.dispatch_job", return_value="celery") as mock_dispatch:
            result = jobs_router.fix_errors(db=db)

        self.assertCountEqual(result["fixed"], [dead_id, limit_id])
        self.assertEqual(result["fixed_count"], 2)
        self.assertEqual(mock_dispatch.call_count, 2)

        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(result["skipped"][0]["id"], dup_id)
        self.assertIn("duplicar", result["skipped"][0]["reason"])

        db.refresh(dead_token)
        db.refresh(upload_limit)
        db.refresh(maybe_duplicate)
        self.assertEqual(dead_token.status, JobStatus.QUEUED)
        self.assertIsNone(dead_token.error_message)
        self.assertEqual(upload_limit.status, JobStatus.QUEUED)
        # The duplicate-risk job is untouched — still ERROR with its original message.
        self.assertEqual(maybe_duplicate.status, JobStatus.ERROR)
        self.assertIsNotNone(maybe_duplicate.error_message)

    def test_already_rendered_job_is_republished_not_re_rendered(self) -> None:
        """A job that failed at the PUBLISH step (e.g. token died after the
        video finished rendering) must only be republished — routing it
        through dispatch_job would burn a full AI re-render for nothing."""
        db = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal Teste", niche="geral")
        db.add(account)
        db.flush()

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(b"fake video bytes")
            video_path = tmp.name
        self.addCleanup(lambda: Path(video_path).unlink(missing_ok=True))

        job = VideoJob(
            title="Já renderizado",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.ERROR,
            error_message="HttpError 400 ... 'The user has exceeded the number of videos they may upload.'",
            main_video_path=video_path,
        )
        db.add(job)
        db.commit()
        job_id = job.id

        with patch("backend.routers.jobs.dispatch_job") as mock_dispatch_job, patch(
            "backend.routers.jobs.dispatch_publish", return_value="celery"
        ) as mock_dispatch_publish:
            result = jobs_router.fix_errors(db=db)

        self.assertEqual(result["fixed"], [job_id])
        mock_dispatch_publish.assert_called_once_with(job_id)
        mock_dispatch_job.assert_not_called()

        db.refresh(job)
        self.assertEqual(job.status, JobStatus.APPROVED)
        self.assertEqual(job.approval_status, "approved")
        self.assertIsNone(job.error_message)

    def test_no_error_jobs_is_a_clean_no_op(self) -> None:
        db = _make_session()
        with patch("backend.routers.jobs.dispatch_job") as mock_dispatch:
            result = jobs_router.fix_errors(db=db)
        self.assertEqual(result, {"fixed": [], "fixed_count": 0, "skipped": [], "skipped_count": 0})
        mock_dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
