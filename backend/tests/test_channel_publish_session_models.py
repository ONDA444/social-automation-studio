"""Regression tests for the new Channel / PublishSession orchestration layer
(Fase 2 of the sessions/agenda/channels/design spec).

Channel wraps a PlatformAccount 1:1 -- it does not duplicate identity/OAuth/
quota, only the orchestration config the account never had (visual_theme,
daily long/short caps, posting window). PublishSession tracks REFERENCES
(video_job ids) into VideoJob, never a copy of job state, so the actual
dispatch/publish queue in scheduler.py has exactly one source of truth.
"""
from __future__ import annotations

import unittest
from datetime import date, datetime

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.database import Base, ensure_columns, ensure_indexes
from backend.models import (
    Channel,
    JobStatus,
    PlatformAccount,
    PublishSession,
    VideoJob,
)
from backend.models.channel import DEFAULT_VISUAL_THEME


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session(), engine


class ChannelModelTests(unittest.TestCase):
    def _account(self, db):
        account = PlatformAccount(platform="youtube", display_name="Canal", niche="geral")
        db.add(account)
        db.flush()
        return account

    def test_channel_wraps_one_account(self) -> None:
        db, _ = _make_session()
        account = self._account(db)

        channel = Channel(account_id=account.id, name="Canal Teste")
        db.add(channel)
        db.commit()

        self.assertEqual(channel.daily_limit_long, 1)
        self.assertEqual(channel.daily_limit_short, 3)
        self.assertEqual(channel.posting_window_start, "08:00")
        self.assertTrue(channel.active)

    def test_second_channel_for_the_same_account_is_rejected(self) -> None:
        """One Channel per PlatformAccount -- account_id is unique."""
        db, _ = _make_session()
        account = self._account(db)
        db.add(Channel(account_id=account.id, name="Primeiro"))
        db.commit()

        db.add(Channel(account_id=account.id, name="Segundo"))
        with self.assertRaises(IntegrityError):
            db.commit()

    def test_resolved_visual_theme_merges_over_defaults(self) -> None:
        db, _ = _make_session()
        account = self._account(db)
        channel = Channel(
            account_id=account.id, name="Canal Teste",
            visual_theme={"accent_color": "#00FFAA"},
        )
        db.add(channel)
        db.commit()

        resolved = channel.resolved_visual_theme()
        self.assertEqual(resolved["accent_color"], "#00FFAA")
        # Untouched keys still come from the shared default.
        self.assertEqual(resolved["stroke_color"], DEFAULT_VISUAL_THEME["stroke_color"])

    def test_channel_with_no_override_returns_the_bare_defaults(self) -> None:
        db, _ = _make_session()
        account = self._account(db)
        channel = Channel(account_id=account.id, name="Canal Teste")
        db.add(channel)
        db.commit()

        self.assertEqual(channel.resolved_visual_theme(), DEFAULT_VISUAL_THEME)


class PublishSessionModelTests(unittest.TestCase):
    def _channel(self, db):
        account = PlatformAccount(platform="youtube", display_name="Canal", niche="geral")
        db.add(account)
        db.flush()
        channel = Channel(account_id=account.id, name="Canal Teste")
        db.add(channel)
        db.flush()
        return channel

    def test_one_session_per_channel_per_day(self) -> None:
        db, _ = _make_session()
        channel = self._channel(db)
        today = date(2026, 8, 2)
        db.add(PublishSession(channel_id=channel.id, date=today))
        db.commit()

        db.add(PublishSession(channel_id=channel.id, date=today))
        with self.assertRaises(IntegrityError):
            db.commit()

    def test_different_days_are_independent_sessions(self) -> None:
        db, _ = _make_session()
        channel = self._channel(db)
        db.add(PublishSession(channel_id=channel.id, date=date(2026, 8, 1)))
        db.add(PublishSession(channel_id=channel.id, date=date(2026, 8, 2)))
        db.commit()  # must NOT raise

        count = db.query(PublishSession).filter_by(channel_id=channel.id).count()
        self.assertEqual(count, 2)

    def test_to_dict_computes_remaining_from_planned_vs_published(self) -> None:
        db, _ = _make_session()
        channel = self._channel(db)
        session = PublishSession(
            channel_id=channel.id, date=date(2026, 8, 2),
            planned_items=[101, 102, 103], published_items=[101],
        )
        db.add(session)
        db.commit()

        data = session.to_dict()
        self.assertEqual(data["remaining"], 2)
        self.assertEqual(data["status"], "pending")

    def test_video_job_can_reference_its_publish_session(self) -> None:
        db, _ = _make_session()
        channel = self._channel(db)
        session = PublishSession(channel_id=channel.id, date=date(2026, 8, 2))
        db.add(session)
        db.flush()

        job = VideoJob(
            title="V", mode="from_ready_video", content_type="film_recap_ai_images",
            video_format="long", account_id=channel.account_id,
            status=JobStatus.QUEUED, publish_session_id=session.id,
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        self.assertEqual(job.publish_session_id, session.id)


class EnsureColumnsMigrationTests(unittest.TestCase):
    """A DB created before this feature existed has no video_jobs.publish_session_id
    column -- ensure_columns() (the same idempotent boot-time migration every other
    late-added column in this codebase uses) must add it without touching existing
    data, exactly like it already does for e.g. schedule_slot_key."""

    def test_publish_session_id_column_is_added_to_a_pre_existing_table(self) -> None:
        import backend.database as db_module

        engine = create_engine("sqlite:///:memory:", future=True)
        # Simulate the OLD schema: create every table via the current models
        # (so all the OTHER columns/tables exist), then drop just the new
        # column to mimic a DB from before this feature shipped.
        Base.metadata.create_all(bind=engine)
        with engine.begin() as conn:
            cols = {r[1] for r in conn.execute(text("PRAGMA table_info(video_jobs)")).fetchall()}
            self.assertIn("publish_session_id", cols)  # sanity: create_all did add it fresh

        # Recreate a bare table lacking the column, mirroring a truly old DB.
        engine2 = create_engine("sqlite:///:memory:", future=True)
        with engine2.begin() as conn:
            conn.execute(text(
                "CREATE TABLE video_jobs (id INTEGER PRIMARY KEY, title TEXT)"
            ))
            cols_before = {r[1] for r in conn.execute(text("PRAGMA table_info(video_jobs)")).fetchall()}
        self.assertNotIn("publish_session_id", cols_before)

        original_engine = db_module.engine
        try:
            db_module.engine = engine2
            ensure_columns()
            ensure_indexes()
        finally:
            db_module.engine = original_engine

        with engine2.begin() as conn:
            cols_after = {r[1] for r in conn.execute(text("PRAGMA table_info(video_jobs)")).fetchall()}
        self.assertIn("publish_session_id", cols_after)


if __name__ == "__main__":
    unittest.main()
