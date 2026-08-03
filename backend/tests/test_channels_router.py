"""Regression tests for the /channels router (Fase 3). Calls the endpoint
functions directly with a db session -- same convention the rest of this
test suite uses for router coverage (see test_theme_queue_position_lock.py)."""
from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import Channel, JobStatus, PlatformAccount, VideoJob
from backend.routers.channels import (
    ChannelCreate,
    ChannelPatch,
    create_channel,
    generate_agenda,
    get_agenda,
    get_channel,
    list_channels,
    patch_channel,
    refresh_channel,
)


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


def _account(db):
    account = PlatformAccount(platform="youtube", display_name="Canal", niche="geral")
    db.add(account)
    db.flush()
    return account


class ChannelCrudTests(unittest.TestCase):
    def test_create_then_get_then_list(self) -> None:
        db = _make_session()
        account = _account(db)

        created = create_channel(
            ChannelCreate(account_id=account.id, name="Canal Teste"), db=db,
        )
        self.assertEqual(created["daily_limit_long"], 1)

        fetched = get_channel(created["id"], db=db)
        self.assertEqual(fetched["name"], "Canal Teste")

        listed = list_channels(db=db)
        self.assertEqual(len(listed["channels"]), 1)

    def test_invalid_intro_mode_is_rejected(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            ChannelCreate(account_id=1, name="Canal", intro_mode="voz_robo_fixa")

    def test_second_channel_for_same_account_is_rejected(self) -> None:
        db = _make_session()
        account = _account(db)
        create_channel(ChannelCreate(account_id=account.id, name="Primeiro"), db=db)

        with self.assertRaises(HTTPException) as ctx:
            create_channel(ChannelCreate(account_id=account.id, name="Segundo"), db=db)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_create_for_missing_account_404s(self) -> None:
        db = _make_session()
        with self.assertRaises(HTTPException) as ctx:
            create_channel(ChannelCreate(account_id=999, name="X"), db=db)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_patch_only_touches_sent_fields(self) -> None:
        db = _make_session()
        account = _account(db)
        created = create_channel(
            ChannelCreate(account_id=account.id, name="Canal Teste", daily_limit_short=5),
            db=db,
        )

        patched = patch_channel(created["id"], ChannelPatch(daily_limit_long=2), db=db)
        self.assertEqual(patched["daily_limit_long"], 2)
        self.assertEqual(patched["daily_limit_short"], 5)  # untouched
        self.assertEqual(patched["name"], "Canal Teste")  # untouched


class AgendaEndpointTests(unittest.TestCase):
    def test_get_agenda_creates_an_empty_session_without_generating_jobs(self) -> None:
        db = _make_session()
        account = _account(db)
        created = create_channel(ChannelCreate(account_id=account.id, name="Canal"), db=db)

        result = get_agenda(created["id"], date="2026-08-02", db=db)

        self.assertEqual(result["session"]["planned_items"], [])
        self.assertEqual(result["jobs"], [])

    def test_get_agenda_for_missing_channel_404s(self) -> None:
        db = _make_session()
        with self.assertRaises(HTTPException) as ctx:
            get_agenda(999, date=None, db=db)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_generate_agenda_starts_a_background_thread_and_returns_immediately(self) -> None:
        db = _make_session()
        account = _account(db)
        created = create_channel(ChannelCreate(account_id=account.id, name="Canal"), db=db)

        with patch("backend.routers.channels.threading.Thread") as fake_thread:
            result = generate_agenda(created["id"], date="2026-08-02", db=db)

        self.assertEqual(result["status"], "started")
        fake_thread.assert_called_once()
        fake_thread.return_value.start.assert_called_once()


class RefreshEndpointTests(unittest.TestCase):
    def test_refresh_without_job_id_starts_a_background_thread_and_returns_immediately(self) -> None:
        db = _make_session()
        account = _account(db)
        created = create_channel(ChannelCreate(account_id=account.id, name="Canal"), db=db)

        with patch("backend.routers.channels.threading.Thread") as fake_thread:
            result = refresh_channel(created["id"], job_id=None, db=db)

        self.assertEqual(result, {"status": "started", "channel_id": created["id"]})
        fake_thread.assert_called_once()
        fake_thread.return_value.start.assert_called_once()

    def test_refresh_with_job_id_targets_just_that_job(self) -> None:
        db = _make_session()
        account = _account(db)
        created = create_channel(ChannelCreate(account_id=account.id, name="Canal"), db=db)
        job = VideoJob(
            title="V", mode="from_ready_video", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status=JobStatus.PUBLISHED,
        )
        db.add(job)
        db.commit()

        with patch("backend.agents.channel_refresh.refresh_job",
                   return_value={"job_id": job.id, "action": "refreshed"}) as fake:
            result = refresh_channel(created["id"], job_id=job.id, db=db)

        fake.assert_called_once()
        self.assertEqual(result["results"], [{"job_id": job.id, "action": "refreshed"}])

    def test_refresh_for_a_job_belonging_to_a_different_channel_404s(self) -> None:
        db = _make_session()
        account1 = _account(db)
        account2 = _account(db)
        created = create_channel(ChannelCreate(account_id=account1.id, name="Canal 1"), db=db)
        other_job = VideoJob(
            title="V", mode="from_ready_video", content_type="film_recap_ai_images",
            video_format="long", account_id=account2.id, status=JobStatus.PUBLISHED,
        )
        db.add(other_job)
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            refresh_channel(created["id"], job_id=other_job.id, db=db)
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
