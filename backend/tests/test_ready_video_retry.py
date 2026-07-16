from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import scheduler
from backend.database import Base
from backend.models import JobStatus, PlatformAccount, ReadyVideo, VideoJob
from backend.pipeline import dispatch


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session(), Session


class ReadyVideoRetryRoutingTests(unittest.TestCase):
    """A `from_ready_video` (Drive) job must NEVER be re-dispatched through the
    full AI orchestrator — that would silently replace the user's own Drive
    file with a brand-new AI-scripted video on retry. See dispatch.py's
    `_is_ready_video_job` guard."""

    def test_dispatch_job_routes_ready_video_mode_away_from_orchestrator(self) -> None:
        db, Session = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal", video_source_mode="drive")
        db.add(account)
        db.flush()
        job = VideoJob(
            title="Video do Drive",
            mode="from_ready_video",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.ERROR,
        )
        db.add(job)
        db.commit()

        with patch("backend.database.SessionLocal", Session), patch.object(
            dispatch, "dispatch_retry_ready_video"
        ) as fake_retry, patch(
            "backend.agents.orchestrator.run_pipeline"
        ) as fake_pipeline:
            fake_retry.return_value = "in_process"
            transport = dispatch.dispatch_job(job.id)

        self.assertEqual(transport, "in_process")
        fake_retry.assert_called_once_with(job.id)
        fake_pipeline.assert_not_called()


class RetryReadyVideoJobTests(unittest.TestCase):
    def test_retry_reuses_the_original_drive_file_not_ai_generation(self) -> None:
        db, Session = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal", video_source_mode="drive")
        db.add(account)
        db.flush()
        ready = ReadyVideo(drive_file_id="abc123", name="corte.mp4", status="reserved")
        db.add(ready)
        db.flush()
        job = VideoJob(
            title="Video do Drive: espera o final #Shorts",
            mode="from_ready_video",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.ERROR,
            error_message="Falha ao baixar video do Drive: timeout",
            video_context={"source": "drive_ready_video", "ready_video_id": ready.id},
        )
        db.add(job)
        db.commit()
        job_id, ready_id = job.id, ready.id

        with patch("backend.scheduler.SessionLocal", Session), patch(
            "backend.agents.drive_library.DriveLibraryService.download_for_job",
            return_value="/tmp/fake.mp4",
        ), patch(
            "backend.agents.ready_video_seo.build_ready_video_package",
            return_value=({"analysis_source": "probe_fallback"}, {"youtube": {"title": "Titulo real"}}, None),
        ), patch(
            "backend.pipeline.dispatch.dispatch_publish"
        ), patch(
            "backend.agents.orchestrator.run_pipeline"
        ) as fake_pipeline:
            scheduler.retry_ready_video_job(job_id)

        db2 = Session()
        refreshed_job = db2.get(VideoJob, job_id)
        refreshed_ready = db2.get(ReadyVideo, ready_id)
        self.assertIn(refreshed_job.status, (JobStatus.APPROVED, JobStatus.PUBLISHING))
        self.assertEqual(refreshed_job.title, "Titulo real")
        # The reservation must still point at the SAME Drive file — retry never
        # substitutes AI-generated content for the user's own video.
        self.assertEqual(refreshed_job.video_context.get("ready_video_id"), ready_id)
        self.assertEqual(refreshed_ready.status, "reserved")
        fake_pipeline.assert_not_called()

    def test_retry_without_a_resolvable_ready_video_lands_in_error(self) -> None:
        db, Session = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal", video_source_mode="drive")
        db.add(account)
        db.flush()
        job = VideoJob(
            title="Video do Drive",
            mode="from_ready_video",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.ERROR,
            video_context={"source": "drive_ready_video", "ready_video_id": 999999},
        )
        db.add(job)
        db.commit()
        job_id = job.id

        with patch("backend.scheduler.SessionLocal", Session):
            scheduler.retry_ready_video_job(job_id)

        db2 = Session()
        refreshed = db2.get(VideoJob, job_id)
        self.assertEqual(refreshed.status, JobStatus.ERROR)


if __name__ == "__main__":
    unittest.main()
