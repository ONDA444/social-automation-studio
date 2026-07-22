from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import scheduler
from backend.agents.drive_library import DriveLibraryService
from backend.database import Base
from backend.models import JobStatus, PlatformAccount, ReadyVideo, VideoJob


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


class ReadyVideoFinalizeRetryCapTests(unittest.TestCase):
    """Production had the same Drive file end up as 2-4 SEPARATE VideoJob rows,
    each independently uploaded to YouTube. Root cause: every failed download
    used to release the ready_video back to "available" immediately, letting
    a completely different theme grab the SAME file and create a brand-new
    job while the original failed job was ALSO still eligible for its own
    retry — two (or more) independent attempts at one source file. The fix
    caps retries on the SAME job before releasing the file back to the pool."""

    def _setup(self):
        db = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal", niche="geral")
        db.add(account)
        db.flush()
        ready = ReadyVideo(
            drive_file_id="abc123",
            name="corte.mp4",
            content_type="film_recap_ai_images",
            video_format="short",
            account_id=account.id,
            status="reserved",
        )
        db.add(ready)
        db.flush()
        job = VideoJob(
            title="Video do Drive",
            mode="from_ready_video",
            content_type="film_recap_ai_images",
            video_format="short",
            account_id=account.id,
            status=JobStatus.PROCESSING,
            video_context={"source": "drive_ready_video", "ready_video_id": ready.id, "drive_file_id": ready.drive_file_id},
        )
        db.add(job)
        db.flush()
        ready.reserved_job_id = job.id
        db.commit()
        return db, account, ready, job

    def test_first_failure_keeps_the_reservation_pointing_at_the_same_job(self) -> None:
        db, account, ready, job = self._setup()
        drive = DriveLibraryService(db)

        with patch.object(DriveLibraryService, "download_for_job", side_effect=RuntimeError("timeout de rede")):
            scheduler._finalize_ready_video_job(
                db, drive, job, ready, account, title_seed="Video do Drive", video_format="short",
            )

        db.refresh(job)
        db.refresh(ready)
        self.assertEqual(job.status, JobStatus.ERROR)
        self.assertEqual(job.retry_count, 1)
        self.assertNotIn("desistindo apos", job.error_message)
        # Still reserved by THIS job — must not be up for grabs by a different theme.
        self.assertEqual(ready.status, "reserved")
        self.assertEqual(ready.reserved_job_id, job.id)

    def test_second_failure_releases_the_file_and_marks_the_job_non_retryable(self) -> None:
        db, account, ready, job = self._setup()
        job.retry_count = 1  # simulate the first failure already having happened
        db.commit()
        drive = DriveLibraryService(db)

        with patch.object(DriveLibraryService, "download_for_job", side_effect=RuntimeError("timeout de rede")):
            scheduler._finalize_ready_video_job(
                db, drive, job, ready, account, title_seed="Video do Drive", video_format="short",
            )

        db.refresh(job)
        db.refresh(ready)
        self.assertEqual(job.status, JobStatus.ERROR)
        self.assertEqual(job.retry_count, 2)
        self.assertIn("desistindo apos", job.error_message)
        # NOW released for a fresh attempt by a different theme/job.
        self.assertEqual(ready.status, "available")
        self.assertIsNone(ready.reserved_job_id)

    def test_exhausted_marker_is_excluded_from_blind_auto_retry(self) -> None:
        markers = " ".join(scheduler._NO_AUTO_RETRY_MARKERS)
        self.assertIn("desistindo apos", markers)


if __name__ == "__main__":
    unittest.main()
