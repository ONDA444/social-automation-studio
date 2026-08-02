"""Regression test: _finalize_ready_video_job must look up the account's
Channel (if one exists) and pass its visual_theme/tts_voice into
apply_curation_layer -- otherwise every channel keeps sharing the fixed
yellow-on-black look/default voice even after a Channel row is configured.
Accounts with NO Channel must be unaffected (visual_theme=None, voice=None,
preserving the pre-Channel behaviour exactly)."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import Channel, JobStatus, PlatformAccount, ReadyVideo, VideoJob


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


def _setup(db, *, with_channel: bool):
    account = PlatformAccount(platform="youtube", display_name="Canal", niche="geral")
    db.add(account)
    db.flush()
    channel = None
    if with_channel:
        channel = Channel(
            account_id=account.id, name="Canal Teste",
            visual_theme={"accent_color": "#00AAFF"}, tts_voice="pt-BR-FabioNeural",
        )
        db.add(channel)
        db.flush()
    ready = ReadyVideo(
        drive_file_id="clip-abc", name="clip.mp4", mime_type="video/mp4",
        content_type="film_recap_ai_images", video_format="long",
        account_id=account.id, status="reserved",
    )
    db.add(ready)
    db.flush()
    job = VideoJob(
        title="Video", mode="from_ready_video", content_type="film_recap_ai_images",
        video_format="long", account_id=account.id, status=JobStatus.PROCESSING,
        video_context={"source": "drive_ready_video", "ready_video_id": ready.id},
    )
    db.add(job)
    db.flush()
    ready.reserved_job_id = job.id
    db.commit()
    return db, account, channel, ready, job


class SchedulerPassesChannelThemeToVoiceTests(unittest.TestCase):
    def test_channel_visual_theme_and_voice_reach_the_curation_layer(self) -> None:
        from backend import scheduler
        from backend.agents.drive_library import DriveLibraryService

        db, account, channel, ready, job = _setup(db=_make_session(), with_channel=True)
        fake_seo = {"youtube": {"title": "T", "description": "d", "tags": [],
                                 "category_id": "24"}, "tiktok": {}, "instagram": {}}
        captured = {}

        def fake_apply_curation(**kwargs):
            captured.update(kwargs)
            return kwargs["local_path"]

        with patch.object(DriveLibraryService, "download_for_job", return_value="/tmp/clip.mp4"), \
             patch("backend.agents.ready_video_seo.build_ready_video_package",
                   return_value=({}, fake_seo, None)), \
             patch("backend.agents.ready_video_curation.apply_curation_layer", fake_apply_curation), \
             patch("backend.pipeline.dispatch.dispatch_publish"):
            drive = DriveLibraryService(db)
            scheduler._finalize_ready_video_job(
                db, drive, job, ready, account, title_seed="Video", video_format="long",
            )

        # Merged over Channel.DEFAULT_VISUAL_THEME -- only accent_color was
        # overridden, everything else still comes from the shared default.
        self.assertEqual(captured.get("visual_theme"), channel.resolved_visual_theme())
        self.assertEqual(captured["visual_theme"]["accent_color"], "#00AAFF")
        self.assertEqual(captured.get("voice"), "pt-BR-FabioNeural")

    def test_account_without_a_channel_gets_no_theme_or_voice_override(self) -> None:
        from backend import scheduler
        from backend.agents.drive_library import DriveLibraryService

        db, account, channel, ready, job = _setup(db=_make_session(), with_channel=False)
        fake_seo = {"youtube": {"title": "T", "description": "d", "tags": [],
                                 "category_id": "24"}, "tiktok": {}, "instagram": {}}
        captured = {}

        def fake_apply_curation(**kwargs):
            captured.update(kwargs)
            return kwargs["local_path"]

        with patch.object(DriveLibraryService, "download_for_job", return_value="/tmp/clip.mp4"), \
             patch("backend.agents.ready_video_seo.build_ready_video_package",
                   return_value=({}, fake_seo, None)), \
             patch("backend.agents.ready_video_curation.apply_curation_layer", fake_apply_curation), \
             patch("backend.pipeline.dispatch.dispatch_publish"):
            drive = DriveLibraryService(db)
            scheduler._finalize_ready_video_job(
                db, drive, job, ready, account, title_seed="Video", video_format="long",
            )

        self.assertIsNone(captured.get("visual_theme"))
        self.assertIsNone(captured.get("voice"))


if __name__ == "__main__":
    unittest.main()
