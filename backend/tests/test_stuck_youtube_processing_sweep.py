from __future__ import annotations

import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import scheduler
from backend.agents.account_profile import AccountProfileService
from backend.database import Base
from backend.models import JobStatus, PlatformAccount, VideoJob


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session(), Session


class StuckYoutubeProcessingSweepTests(unittest.TestCase):
    """upload_video() only confirms the API accepted the bytes — it can never
    see a video that stays 'Pendente'/processing forever on YouTube's side.
    This sweep is the only thing that can ever catch that, so it must (a)
    actually flag a video still stuck past the threshold, (b) leave a
    genuinely-finished video alone, (c) never re-check the same video twice,
    and (d) skip videos outside its recency window entirely."""

    def _account_and_job(self, db, *, video_id, hours_ago, checked=False):
        account = db.query(PlatformAccount).filter_by(display_name="Canal Teste").first()
        if not account:
            account = PlatformAccount(platform="youtube", display_name="Canal Teste", niche="geral")
            db.add(account)
            db.flush()
        yt = {"ok": True, "video_id": video_id, "status": "published"}
        if checked:
            yt["processing_checked"] = True
        job = VideoJob(
            title=f"Video {video_id}",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="short",
            account_id=account.id,
            status=JobStatus.PUBLISHED,
            publish_status={"youtube": yt},
            updated_at=datetime.utcnow() - timedelta(hours=hours_ago),
        )
        db.add(job)
        db.commit()
        return job.id

    def test_video_stuck_past_threshold_gets_flagged(self) -> None:
        db, Session = _make_session()
        job_id = self._account_and_job(db, video_id="vid_stuck", hours_ago=30)

        with patch.object(scheduler, "SessionLocal", Session), \
             patch.object(AccountProfileService, "get_credentials", return_value={"access_token": "x"}), \
             patch("backend.uploaders.youtube.get_video_processing_status",
                   return_value={"found": True, "upload_status": "uploaded"}):
            scheduler._job_check_stuck_youtube_processing()

        refreshed = Session().get(VideoJob, job_id)
        yt = refreshed.publish_status["youtube"]
        self.assertTrue(yt["processing_checked"])
        self.assertTrue(yt["stuck_processing"])
        # Still PUBLISHED — the upload genuinely happened; flipping to ERROR
        # here would risk the retry machinery re-rendering a duplicate.
        self.assertEqual(refreshed.status, JobStatus.PUBLISHED)

    def test_video_that_finished_processing_is_not_flagged(self) -> None:
        db, Session = _make_session()
        job_id = self._account_and_job(db, video_id="vid_fine", hours_ago=30)

        with patch.object(scheduler, "SessionLocal", Session), \
             patch.object(AccountProfileService, "get_credentials", return_value={"access_token": "x"}), \
             patch("backend.uploaders.youtube.get_video_processing_status",
                   return_value={"found": True, "upload_status": "processed"}):
            scheduler._job_check_stuck_youtube_processing()

        refreshed = Session().get(VideoJob, job_id)
        yt = refreshed.publish_status["youtube"]
        self.assertTrue(yt["processing_checked"])
        self.assertNotIn("stuck_processing", yt)

    def test_already_checked_video_is_never_re_checked(self) -> None:
        db, Session = _make_session()
        job_id = self._account_and_job(db, video_id="vid_done", hours_ago=30, checked=True)

        with patch.object(scheduler, "SessionLocal", Session), \
             patch.object(AccountProfileService, "get_credentials", return_value={"access_token": "x"}), \
             patch("backend.uploaders.youtube.get_video_processing_status") as fake_check:
            scheduler._job_check_stuck_youtube_processing()

        fake_check.assert_not_called()

    def test_video_published_too_recently_is_not_yet_checked(self) -> None:
        db, Session = _make_session()
        job_id = self._account_and_job(db, video_id="vid_new", hours_ago=1)

        with patch.object(scheduler, "SessionLocal", Session), \
             patch.object(AccountProfileService, "get_credentials", return_value={"access_token": "x"}), \
             patch("backend.uploaders.youtube.get_video_processing_status") as fake_check:
            scheduler._job_check_stuck_youtube_processing()

        fake_check.assert_not_called()

    def test_stalled_call_times_out_without_hanging_the_sweep(self) -> None:
        """A stalled googleapiclient call must never hang the sweep forever —
        it should give up after _STUCK_YT_CHECK_CALL_TIMEOUT_S and leave the
        job unmarked so a later tick tries again."""
        db, Session = _make_session()
        job_id = self._account_and_job(db, video_id="vid_hangs", hours_ago=30)

        def _hangs(video_id, credentials):
            time.sleep(0.3)
            return {"found": True, "upload_status": "processed"}

        with patch.object(scheduler, "SessionLocal", Session), \
             patch.object(scheduler, "_STUCK_YT_CHECK_CALL_TIMEOUT_S", 0.05), \
             patch.object(AccountProfileService, "get_credentials", return_value={"access_token": "x"}), \
             patch("backend.uploaders.youtube.get_video_processing_status", side_effect=_hangs):
            scheduler._job_check_stuck_youtube_processing()

        refreshed = Session().get(VideoJob, job_id)
        yt = refreshed.publish_status["youtube"]
        self.assertNotIn("processing_checked", yt)

    def test_checks_per_tick_are_capped(self) -> None:
        db, Session = _make_session()
        total = scheduler._STUCK_YT_CHECK_BATCH_LIMIT + 5
        for i in range(total):
            self._account_and_job(db, video_id=f"vid_{i}", hours_ago=30)

        with patch.object(scheduler, "SessionLocal", Session), \
             patch.object(AccountProfileService, "get_credentials", return_value={"access_token": "x"}), \
             patch("backend.uploaders.youtube.get_video_processing_status",
                   return_value={"found": True, "upload_status": "processed"}) as fake_check:
            scheduler._job_check_stuck_youtube_processing()

        self.assertEqual(fake_check.call_count, scheduler._STUCK_YT_CHECK_BATCH_LIMIT)


if __name__ == "__main__":
    unittest.main()
