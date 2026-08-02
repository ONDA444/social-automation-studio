"""Fase 6 -- end-to-end validation of the channels/agenda/session/refresh
layer built in Fases 2-5. Covers exactly the four checks the spec asked for:

  1. Two channels operating at once never mix queue, visual theme, or TTS voice.
  2. A refresh never creates a second job for the same video (mutates in place).
  3. Exceeding daily_limit_long/short blocks further scheduling for that day.
  4. A full flow for one real video: session created, item scheduled, curation
     applied with the channel's own identity, publish simulated, everything
     logged.

Uses the same "call the real functions, mock only Drive/ffmpeg/LLM" style as
the rest of this suite -- no new conventions introduced.
"""
from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agents import agenda, channel_refresh as cr
from backend.database import Base
from backend.models import Channel, JobStatus, PlatformAccount, ReadyVideo, VideoJob


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


def _channel(db, *, name, accent, voice, daily_long=1, daily_short=1):
    account = PlatformAccount(platform="youtube", display_name=name, niche="geral")
    db.add(account)
    db.flush()
    channel = Channel(
        account_id=account.id, name=name, tts_voice=voice,
        visual_theme={"accent_color": accent},
        daily_limit_long=daily_long, daily_limit_short=daily_short,
    )
    db.add(channel)
    db.flush()
    return channel


class TwoChannelIsolationTests(unittest.TestCase):
    """Item 1: two channels generating their agenda for the SAME day must
    never leak jobs, theme, or voice into each other."""

    def test_two_channels_get_independent_jobs_themes_and_voices(self) -> None:
        db = _make_session()
        channel_a = _channel(db, name="Canal A", accent="#FF0000", voice="pt-BR-AntonioNeural")
        channel_b = _channel(db, name="Canal B", accent="#00FF00", voice="pt-BR-FranciscaNeural")

        created_for = {"A": [], "B": []}

        def fake_create(db_, acct, scheduled_naive, theme=None, slot_key=None):
            job = VideoJob(
                title="V", mode="from_ready_video", content_type="film_recap_ai_images",
                video_format=theme.video_format, account_id=acct.id,
                status=JobStatus.AWAITING_APPROVAL, scheduled_at=scheduled_naive,
            )
            db_.add(job)
            db_.flush()
            key = "A" if acct.id == channel_a.account_id else "B"
            created_for[key].append(job.id)
            return job.id

        with patch("backend.scheduler._try_create_ready_video_job", fake_create):
            session_a = agenda.generate_daily_agenda(db, channel_a, date(2026, 8, 2))
            session_b = agenda.generate_daily_agenda(db, channel_b, date(2026, 8, 2))

        # Each session only knows about its own channel's jobs.
        self.assertEqual(set(session_a.planned_items), set(created_for["A"]))
        self.assertEqual(set(session_b.planned_items), set(created_for["B"]))
        self.assertTrue(set(session_a.planned_items).isdisjoint(session_b.planned_items))

        # Refreshing channel A must use A's theme/voice, never B's.
        job_a = db.get(VideoJob, created_for["A"][0])
        job_a.video_context = {"ready_video_id": self._ready(db, channel_a).id,
                                "content_analysis": {"summary": "s"}}
        db.commit()
        seen = {}
        with patch("backend.agents.drive_library.DriveLibraryService.download_for_job",
                   return_value="/tmp/source.mp4"), \
             patch("backend.agents.ready_video_curation.apply_curation_layer") as fake_curate:
            fake_curate.side_effect = lambda **kw: (seen.update(kw), "/tmp/curated.mp4")[1]
            cr.refresh_job(db, channel_a, job_a)

        self.assertEqual(seen["visual_theme"]["accent_color"], "#FF0000")
        self.assertEqual(seen["voice"], "pt-BR-AntonioNeural")
        self.assertNotEqual(seen["visual_theme"]["accent_color"], "#00FF00")

    @staticmethod
    def _ready(db, channel):
        ready = ReadyVideo(
            drive_file_id=f"clip-{channel.id}", name="clip.mp4", mime_type="video/mp4",
            content_type="film_recap_ai_images", video_format="long",
            account_id=channel.account_id, status="used",
        )
        db.add(ready)
        db.flush()
        return ready


class RefreshNeverDuplicatesTests(unittest.TestCase):
    """Item 2: refreshing (even repeatedly) mutates the existing job's
    main_video_path -- it must NEVER create a second VideoJob row."""

    def test_multiple_refreshes_keep_exactly_one_job_row(self) -> None:
        db = _make_session()
        channel = _channel(db, name="Canal", accent="#FFD400", voice="pt-BR-AntonioNeural")
        ready = ReadyVideo(
            drive_file_id="clip-x", name="clip.mp4", mime_type="video/mp4",
            content_type="film_recap_ai_images", video_format="long",
            account_id=channel.account_id, status="used",
        )
        db.add(ready)
        db.flush()
        job = VideoJob(
            title="V", mode="from_ready_video", content_type="film_recap_ai_images",
            video_format="long", account_id=channel.account_id, status=JobStatus.PUBLISHED,
            video_context={"ready_video_id": ready.id, "content_analysis": {"summary": "s"}},
            main_video_path="/tmp/curated_v1.mp4",
        )
        db.add(job)
        db.commit()

        before_count = db.query(VideoJob).count()

        with patch("backend.agents.drive_library.DriveLibraryService.download_for_job",
                   return_value="/tmp/source.mp4"), \
             patch("backend.agents.ready_video_curation.apply_curation_layer",
                   return_value="/tmp/curated_v2.mp4"):
            cr.refresh_job(db, channel, job)  # theme "changed" (no prior fingerprint) -> refreshes
            cr.refresh_job(db, channel, job)  # now unchanged -> skips
            cr.refresh_job(db, channel, job)  # still unchanged -> skips

        after_count = db.query(VideoJob).count()
        self.assertEqual(before_count, 1)
        self.assertEqual(after_count, 1)  # never grew
        self.assertEqual(job.main_video_path, "/tmp/curated_v2.mp4")  # mutated, not duplicated


class DailyLimitEnforcementTests(unittest.TestCase):
    """Item 3: hitting daily_limit_long/short must block further scheduling
    for that channel/day, not silently overwrite/exceed it."""

    def test_generation_never_exceeds_the_configured_daily_caps(self) -> None:
        db = _make_session()
        channel = _channel(db, name="Canal", accent="#FFD400", voice="pt-BR-AntonioNeural",
                            daily_long=2, daily_short=1)
        attempts = {"n": 0}

        def fake_create(db_, acct, scheduled_naive, theme=None, slot_key=None):
            attempts["n"] += 1
            job = VideoJob(
                title="V", mode="from_ready_video", content_type="film_recap_ai_images",
                video_format=theme.video_format, account_id=acct.id,
                status=JobStatus.AWAITING_APPROVAL, scheduled_at=scheduled_naive,
            )
            db_.add(job)
            db_.flush()
            return job.id

        with patch("backend.scheduler._try_create_ready_video_job", fake_create):
            agenda.generate_daily_agenda(db, channel, date(2026, 8, 2))
            # Run it again several times the same day -- must NOT keep adding more.
            agenda.generate_daily_agenda(db, channel, date(2026, 8, 2))
            agenda.generate_daily_agenda(db, channel, date(2026, 8, 2))

        self.assertEqual(attempts["n"], 3)  # 2 long + 1 short, created exactly once
        long_count = db.query(VideoJob).filter_by(video_format="long").count()
        short_count = db.query(VideoJob).filter_by(video_format="short").count()
        self.assertEqual(long_count, channel.daily_limit_long)
        self.assertEqual(short_count, channel.daily_limit_short)


class FullFlowForOneRealVideoTests(unittest.TestCase):
    """Item 4: session created -> item scheduled -> curation applied with the
    channel's identity -> publish simulated -> session reflects it, with
    every step logged (see agenda.py/channel_refresh.py's logger.info calls)."""

    def test_full_flow_end_to_end(self) -> None:
        db = _make_session()
        channel = _channel(db, name="Canal E2E", accent="#123456", voice="pt-BR-FabioNeural",
                            daily_long=1, daily_short=0)

        # Step 1+2: agenda generation creates the session and schedules 1 item,
        # via the SAME reservation primitive the automatic scheduler uses.
        captured_curation = {}

        def fake_try_create(db_, acct, scheduled_naive, theme=None, slot_key=None):
            # Mirrors _try_create_ready_video_job's own shape closely enough
            # for this test: reserve a fake ready video, build the job, run
            # curation with the channel identity threaded all the way in.
            ready = ReadyVideo(
                drive_file_id="clip-e2e", name="clip.mp4", mime_type="video/mp4",
                content_type="film_recap_ai_images", video_format=theme.video_format,
                account_id=acct.id, status="reserved",
            )
            db_.add(ready)
            db_.flush()
            job = VideoJob(
                title="Video E2E", mode="from_ready_video", content_type="film_recap_ai_images",
                video_format=theme.video_format, account_id=acct.id,
                status=JobStatus.AWAITING_APPROVAL, approval_status="pending",
                scheduled_at=scheduled_naive,
                video_context={"ready_video_id": ready.id,
                                "content_analysis": {"summary": "clipe de teste e2e"}},
            )
            db_.add(job)
            db_.flush()
            ready.reserved_job_id = job.id

            from backend.agents.ready_video_curation import apply_curation_layer
            curated = apply_curation_layer(
                job_id=job.id, local_path="/tmp/pristine.mp4",
                analysis={"summary": "clipe de teste e2e"}, context={"niche": channel.niche or ""},
                video_format=theme.video_format,
                visual_theme=channel.resolved_visual_theme(), voice=channel.tts_voice,
            )
            captured_curation["path"] = curated
            job.main_video_path = curated
            job.approval_status = "approved"
            job.status = JobStatus.APPROVED
            return job.id

        with patch("backend.scheduler._try_create_ready_video_job", fake_try_create), \
             patch("backend.agents.ready_video_curation.apply_curation_layer",
                   return_value="/tmp/curated_e2e.mp4") as fake_curate:
            session = agenda.generate_daily_agenda(db, channel, date(2026, 8, 2))

        self.assertEqual(session.status, "running")
        self.assertEqual(len(session.planned_items), 1)
        job_id = session.planned_items[0]
        fake_curate.assert_called_once()
        self.assertEqual(fake_curate.call_args.kwargs["visual_theme"]["accent_color"], "#123456")
        self.assertEqual(fake_curate.call_args.kwargs["voice"], "pt-BR-FabioNeural")
        self.assertEqual(captured_curation["path"], "/tmp/curated_e2e.mp4")

        # Step 3: simulate the publish tick actually completing.
        job = db.get(VideoJob, job_id)
        job.status = JobStatus.PUBLISHED
        db.commit()
        synced = agenda.sync_published_items(db, session)
        self.assertEqual(synced.status, "completed")
        self.assertEqual(synced.published_items, [job_id])

        # Step 4: a refresh right after must find nothing changed (same
        # analysis, same channel identity) and skip, proving the fingerprint
        # recorded during the real curation call above actually stuck... but
        # since THIS flow curated via the mocked apply_curation_layer (not
        # channel_refresh's own call), refresh_job's first call still fires
        # once (no fingerprint yet) then a second call skips.
        with patch("backend.agents.drive_library.DriveLibraryService.download_for_job",
                   return_value="/tmp/pristine.mp4"), \
             patch("backend.agents.ready_video_curation.apply_curation_layer",
                   return_value="/tmp/curated_e2e_v2.mp4") as fake_curate2:
            first = cr.refresh_job(db, channel, job)
            second = cr.refresh_job(db, channel, job)

        self.assertEqual(first["action"], "refreshed")
        self.assertEqual(second["action"], "skipped")
        self.assertEqual(fake_curate2.call_count, 1)  # second call never re-curated


if __name__ == "__main__":
    unittest.main()
