"""Regression tests for the agenda generator (Fase 3 of the sessions/agenda/
channels/design spec). generate_daily_agenda() must respect a channel's daily
long/short caps, stay idempotent across repeated calls, and count jobs from
ANY source (not just its own PublishSession) so it never double-books a day
that the pre-existing automatic ThemeQueue scheduler is also filling."""
from __future__ import annotations

import unittest
from datetime import date, datetime, time
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agents import agenda
from backend.database import Base
from backend.models import Channel, JobStatus, PlatformAccount, PublishSession, VideoJob


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


def _channel(db, **overrides):
    account = PlatformAccount(platform="youtube", display_name="Canal", niche="geral")
    db.add(account)
    db.flush()
    kwargs = {"account_id": account.id, "name": "Canal Teste",
              "daily_limit_long": 1, "daily_limit_short": 2,
              "posting_window_start": "08:00", "posting_window_end": "20:00"}
    kwargs.update(overrides)
    channel = Channel(**kwargs)
    db.add(channel)
    db.flush()
    return channel


def _job(db, account_id, video_format, scheduled_at, status=JobStatus.AWAITING_APPROVAL):
    job = VideoJob(
        title="V", mode="from_ready_video", content_type="film_recap_ai_images",
        video_format=video_format, account_id=account_id, status=status,
        scheduled_at=scheduled_at,
    )
    db.add(job)
    db.flush()
    return job


class SlotSpreadTests(unittest.TestCase):
    def test_single_slot_lands_at_window_start(self) -> None:
        slots = agenda._slots_within_window(date(2026, 8, 2), "08:00", "20:00", 1)
        self.assertEqual(slots, [datetime(2026, 8, 2, 8, 0)])

    def test_multiple_slots_are_evenly_spaced(self) -> None:
        slots = agenda._slots_within_window(date(2026, 8, 2), "08:00", "20:00", 3)
        self.assertEqual(len(slots), 3)
        self.assertEqual(slots[0], datetime(2026, 8, 2, 8, 0))
        gaps = [(slots[i + 1] - slots[i]).total_seconds() for i in range(len(slots) - 1)]
        self.assertAlmostEqual(gaps[0], gaps[1], delta=1)

    def test_inverted_window_does_not_crash(self) -> None:
        slots = agenda._slots_within_window(date(2026, 8, 2), "20:00", "08:00", 2)
        self.assertEqual(len(slots), 2)
        self.assertTrue(all(s == slots[0] for s in slots))


class CountScheduledTests(unittest.TestCase):
    def test_counts_matching_format_and_day_excludes_error(self) -> None:
        db = _make_session()
        channel = _channel(db)
        day = date(2026, 8, 2)
        _job(db, channel.account_id, "long", datetime(2026, 8, 2, 10, 0))
        _job(db, channel.account_id, "long", datetime(2026, 8, 2, 15, 0), status=JobStatus.ERROR)
        _job(db, channel.account_id, "short", datetime(2026, 8, 2, 11, 0))
        _job(db, channel.account_id, "long", datetime(2026, 8, 3, 10, 0))  # different day
        db.commit()

        self.assertEqual(agenda._count_scheduled(db, channel.account_id, day, "long"), 1)
        self.assertEqual(agenda._count_scheduled(db, channel.account_id, day, "short"), 1)


class GenerateDailyAgendaTests(unittest.TestCase):
    def test_creates_jobs_up_to_daily_caps(self) -> None:
        db = _make_session()
        channel = _channel(db, daily_limit_long=1, daily_limit_short=2)
        created = {"n": 0}

        def fake_create(db_, acct, scheduled_naive, theme=None, slot_key=None):
            created["n"] += 1
            job = VideoJob(
                title="V", mode="from_ready_video", content_type="film_recap_ai_images",
                video_format=theme.video_format, account_id=acct.id,
                status=JobStatus.AWAITING_APPROVAL, scheduled_at=scheduled_naive,
            )
            db_.add(job)
            db_.flush()
            return job.id

        with patch("backend.scheduler._try_create_ready_video_job", fake_create):
            session = agenda.generate_daily_agenda(db, channel, date(2026, 8, 2))

        self.assertEqual(created["n"], 3)  # 1 long + 2 short
        self.assertEqual(len(session.planned_items), 3)
        self.assertEqual(session.status, "running")

    def test_is_idempotent_on_second_call(self) -> None:
        db = _make_session()
        channel = _channel(db, daily_limit_long=1, daily_limit_short=0)
        calls = {"n": 0}

        def fake_create(db_, acct, scheduled_naive, theme=None, slot_key=None):
            calls["n"] += 1
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
            session2 = agenda.generate_daily_agenda(db, channel, date(2026, 8, 2))

        self.assertEqual(calls["n"], 1)  # second call found the cap already met
        self.assertEqual(len(session2.planned_items), 1)

    def test_respects_jobs_already_scheduled_by_the_automatic_theme_scheduler(self) -> None:
        """A job created by the pre-existing ThemeQueue-driven scheduler (NOT
        via this agenda generator) must still count against the daily cap."""
        db = _make_session()
        channel = _channel(db, daily_limit_long=1, daily_limit_short=0)
        _job(db, channel.account_id, "long", datetime(2026, 8, 2, 9, 0))  # pre-existing
        db.commit()

        with patch("backend.scheduler._try_create_ready_video_job") as fake_create:
            session = agenda.generate_daily_agenda(db, channel, date(2026, 8, 2))

        fake_create.assert_not_called()
        self.assertEqual(session.planned_items, [])

    def test_stops_when_drive_inventory_is_exhausted(self) -> None:
        db = _make_session()
        channel = _channel(db, daily_limit_long=0, daily_limit_short=3)

        with patch("backend.scheduler._try_create_ready_video_job", return_value=None) as fake_create:
            session = agenda.generate_daily_agenda(db, channel, date(2026, 8, 2))

        fake_create.assert_called_once()  # tried once, got None, stopped -- no wasted retries
        self.assertEqual(session.planned_items, [])

    def test_inactive_channel_generates_nothing(self) -> None:
        db = _make_session()
        channel = _channel(db, active=False)

        with patch("backend.scheduler._try_create_ready_video_job") as fake_create:
            session = agenda.generate_daily_agenda(db, channel, date(2026, 8, 2))

        fake_create.assert_not_called()
        self.assertEqual(session.planned_items, [])


class SyncPublishedItemsTests(unittest.TestCase):
    def test_all_published_marks_session_completed(self) -> None:
        db = _make_session()
        channel = _channel(db)
        j1 = _job(db, channel.account_id, "long", datetime(2026, 8, 2, 9, 0), status=JobStatus.PUBLISHED)
        j2 = _job(db, channel.account_id, "short", datetime(2026, 8, 2, 10, 0), status=JobStatus.PUBLISHED)
        session = PublishSession(channel_id=channel.id, date=date(2026, 8, 2),
                                  planned_items=[j1.id, j2.id], status="running")
        db.add(session)
        db.commit()

        result = agenda.sync_published_items(db, session)
        self.assertEqual(result.status, "completed")
        self.assertCountEqual(result.published_items, [j1.id, j2.id])

    def test_mixed_published_and_errored_is_partial_failure(self) -> None:
        db = _make_session()
        channel = _channel(db)
        j1 = _job(db, channel.account_id, "long", datetime(2026, 8, 2, 9, 0), status=JobStatus.PUBLISHED)
        j2 = _job(db, channel.account_id, "short", datetime(2026, 8, 2, 10, 0), status=JobStatus.ERROR)
        session = PublishSession(channel_id=channel.id, date=date(2026, 8, 2),
                                  planned_items=[j1.id, j2.id], status="running")
        db.add(session)
        db.commit()

        result = agenda.sync_published_items(db, session)
        self.assertEqual(result.status, "partial_failure")

    def test_still_in_progress_stays_running(self) -> None:
        db = _make_session()
        channel = _channel(db)
        j1 = _job(db, channel.account_id, "long", datetime(2026, 8, 2, 9, 0), status=JobStatus.PUBLISHED)
        j2 = _job(db, channel.account_id, "short", datetime(2026, 8, 2, 10, 0), status=JobStatus.PROCESSING)
        session = PublishSession(channel_id=channel.id, date=date(2026, 8, 2),
                                  planned_items=[j1.id, j2.id], status="pending")
        db.add(session)
        db.commit()

        result = agenda.sync_published_items(db, session)
        self.assertEqual(result.status, "running")


if __name__ == "__main__":
    unittest.main()
