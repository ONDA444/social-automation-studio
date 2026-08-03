"""Regression tests for the idempotent channel refresh (Fase 4).

refresh_job must: skip ineligible jobs outright, skip jobs whose curation
inputs (video analysis + channel visual_theme/tts_voice) haven't changed
since the last refresh, re-curate from the PRISTINE Drive source (never the
already-curated main_video_path, which would compound), and never regress a
job's video to a worse/blank state when curation produces nothing new.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agents import channel_refresh as cr
from backend.database import Base
from backend.models import Channel, JobStatus, PlatformAccount, ReadyVideo, VideoJob


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


def _setup(db, *, analysis=None, fingerprint=None, status=JobStatus.PUBLISHED,
           mode="from_ready_video", video_format="long"):
    account = PlatformAccount(platform="youtube", display_name="Canal", niche="geral")
    db.add(account)
    db.flush()
    channel = Channel(account_id=account.id, name="Canal Teste",
                       visual_theme={"accent_color": "#FFD400"}, tts_voice="pt-BR-AntonioNeural")
    db.add(channel)
    db.flush()
    ready = ReadyVideo(
        drive_file_id="clip-abc", name="clip.mp4", mime_type="video/mp4",
        content_type="film_recap_ai_images", video_format=video_format,
        account_id=account.id, status="used",
    )
    db.add(ready)
    db.flush()
    ctx = {"source": "drive_ready_video", "ready_video_id": ready.id}
    if analysis is not None:
        ctx["content_analysis"] = analysis
    if fingerprint is not None:
        ctx["curation_fingerprint"] = fingerprint
    job = VideoJob(
        title="Video", mode=mode, content_type="film_recap_ai_images",
        video_format=video_format, account_id=account.id, status=status,
        video_context=ctx, main_video_path="/tmp/curated_old.mp4",
    )
    db.add(job)
    db.commit()
    return db, channel, ready, job


class EligibilityTests(unittest.TestCase):
    def test_non_ready_video_job_is_skipped(self) -> None:
        db, channel, ready, job = _setup(_make_session(), mode="from_title")
        result = cr.refresh_job(db, channel, job)
        self.assertEqual(result["action"], "skipped")
        self.assertEqual(result["reason"], "not_a_ready_video_job")

    def test_in_flight_job_is_skipped(self) -> None:
        db, channel, ready, job = _setup(_make_session(), status=JobStatus.PROCESSING)
        result = cr.refresh_job(db, channel, job)
        self.assertEqual(result["action"], "skipped")
        self.assertIn("status=", result["reason"])

    def test_job_with_no_ready_video_link_is_skipped(self) -> None:
        db = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal", niche="geral")
        db.add(account)
        db.flush()
        channel = Channel(account_id=account.id, name="Canal Teste")
        db.add(channel)
        db.flush()
        job = VideoJob(
            title="V", mode="from_ready_video", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status=JobStatus.PUBLISHED,
            video_context={},
        )
        db.add(job)
        db.commit()

        result = cr.refresh_job(db, channel, job)
        self.assertEqual(result["action"], "skipped")
        self.assertEqual(result["reason"], "no_ready_video_id")


class FingerprintSkipTests(unittest.TestCase):
    def test_matching_fingerprint_skips_without_touching_curation(self) -> None:
        db, channel, ready, job = _setup(
            _make_session(), analysis={"summary": "s", "hook": "h", "topics": ["a"]},
        )
        # Precompute the fingerprint exactly as refresh_job would, store it as
        # if a previous refresh already ran at this exact state.
        job.video_context = {**job.video_context, "curation_fingerprint": cr._fingerprint(job, channel)}
        db.commit()

        with patch("backend.agents.drive_library.DriveLibraryService.download_for_job") as fake_dl:
            result = cr.refresh_job(db, channel, job)

        fake_dl.assert_not_called()
        self.assertEqual(result["action"], "skipped")
        self.assertEqual(result["reason"], "unchanged")

    def test_channel_theme_change_invalidates_the_fingerprint(self) -> None:
        db, channel, ready, job = _setup(
            _make_session(), analysis={"summary": "s"},
            fingerprint="stale-fingerprint-from-before-theme-changed",
        )
        with patch("backend.agents.drive_library.DriveLibraryService.download_for_job",
                   return_value="/tmp/source.mp4") as fake_dl, \
             patch("backend.agents.ready_video_curation.apply_curation_layer",
                   return_value="/tmp/curated_new.mp4") as fake_curate:
            result = cr.refresh_job(db, channel, job)

        fake_dl.assert_called_once()
        fake_curate.assert_called_once()
        self.assertEqual(result["action"], "refreshed")

    def test_intro_mode_change_also_invalidates_the_fingerprint(self) -> None:
        """A channel switching intro_mode (e.g. tts -> mixed, the risk
        mitigation) must be picked up by refresh just like a theme/voice
        change -- it's part of the same fingerprint."""
        db, channel, ready, job = _setup(_make_session(), analysis={"summary": "s"})
        job.video_context = {**job.video_context, "curation_fingerprint": cr._fingerprint(job, channel)}
        db.commit()
        channel.intro_mode = "text_only"
        db.commit()

        with patch("backend.agents.drive_library.DriveLibraryService.download_for_job",
                   return_value="/tmp/source.mp4"), \
             patch("backend.agents.ready_video_curation.apply_curation_layer",
                   return_value="/tmp/curated_new.mp4") as fake_curate:
            result = cr.refresh_job(db, channel, job)

        fake_curate.assert_called_once()
        self.assertEqual(fake_curate.call_args.kwargs["intro_mode"], "text_only")
        self.assertEqual(result["action"], "refreshed")
        self.assertEqual(job.main_video_path, "/tmp/curated_new.mp4")
        self.assertEqual(job.video_context["curation_fingerprint"], cr._fingerprint(job, channel))


class ReCurationSourceTests(unittest.TestCase):
    def test_curates_from_the_pristine_redownload_not_the_already_curated_path(self) -> None:
        """Curating main_video_path (already curated) again would compound
        a second intro onto the first. Must always start from a fresh
        download of the original Drive file."""
        db, channel, ready, job = _setup(_make_session(), analysis={"summary": "s"})
        seen = {}

        def fake_curate(**kwargs):
            seen["local_path"] = kwargs["local_path"]
            return "/tmp/curated_new.mp4"

        with patch("backend.agents.drive_library.DriveLibraryService.download_for_job",
                   return_value="/tmp/pristine_source.mp4"), \
             patch("backend.agents.ready_video_curation.apply_curation_layer", fake_curate):
            cr.refresh_job(db, channel, job)

        self.assertEqual(seen["local_path"], "/tmp/pristine_source.mp4")
        self.assertNotEqual(seen["local_path"], "/tmp/curated_old.mp4")  # the OLD main_video_path


class NoRegressionOnFailureTests(unittest.TestCase):
    def test_curation_producing_nothing_new_leaves_the_existing_video_untouched(self) -> None:
        db, channel, ready, job = _setup(_make_session(), analysis={"summary": "s"})
        original_path = job.main_video_path

        with patch("backend.agents.drive_library.DriveLibraryService.download_for_job",
                   return_value="/tmp/pristine_source.mp4"), \
             patch("backend.agents.ready_video_curation.apply_curation_layer",
                   return_value="/tmp/pristine_source.mp4"):  # curation internally no-op'd
            result = cr.refresh_job(db, channel, job)

        self.assertEqual(result["action"], "no_change_applied")
        self.assertEqual(job.main_video_path, original_path)  # untouched, not blanked
        self.assertNotIn("curation_fingerprint", job.video_context)  # will retry next time

    def test_download_failure_is_reported_and_does_not_touch_the_job(self) -> None:
        db, channel, ready, job = _setup(_make_session(), analysis={"summary": "s"})
        original_path = job.main_video_path

        with patch("backend.agents.drive_library.DriveLibraryService.download_for_job",
                   side_effect=RuntimeError("drive down")):
            result = cr.refresh_job(db, channel, job)

        self.assertEqual(result["action"], "failed")
        self.assertEqual(job.main_video_path, original_path)


class RefreshChannelBatchTests(unittest.TestCase):
    def test_one_bad_job_does_not_stop_the_rest(self) -> None:
        db, channel, ready, job1 = _setup(_make_session(), analysis={"summary": "s1"})
        job2 = VideoJob(
            title="V2", mode="from_ready_video", content_type="film_recap_ai_images",
            video_format="long", account_id=channel.account_id, status=JobStatus.PUBLISHED,
            video_context={"ready_video_id": ready.id, "content_analysis": {"summary": "s2"}},
            main_video_path="/tmp/curated_old2.mp4",
        )
        db.add(job2)
        db.commit()

        call_count = {"n": 0}

        def fake_refresh_job(db_, channel_, job_):
            call_count["n"] += 1
            if job_.id == job1.id:
                raise RuntimeError("boom")
            return {"job_id": job_.id, "action": "refreshed"}

        with patch.object(cr, "refresh_job", fake_refresh_job):
            results = cr.refresh_channel(db, channel)

        self.assertEqual(call_count["n"], 2)  # both attempted despite job1 blowing up
        by_id = {r["job_id"]: r for r in results}
        self.assertEqual(by_id[job1.id]["action"], "failed")
        self.assertEqual(by_id[job2.id]["action"], "refreshed")


if __name__ == "__main__":
    unittest.main()
