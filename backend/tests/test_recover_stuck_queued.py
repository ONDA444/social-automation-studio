from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session as _Session, sessionmaker

from backend import scheduler
from backend.database import Base
from backend.models import JobStatus, PlatformAccount, ReadyVideo, VideoJob


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session(), Session


class RecoverStuckQueuedTests(unittest.TestCase):
    """QUEUED used to be a state nothing periodic ever revisited in
    production (USE_CELERY=true disables the only boot-time redispatch, and
    Celery itself never owns from_ready_video/from_manual_upload jobs) —
    confirmed as 33 jobs stuck for up to 10 days. This sweep is the safety
    net that finally claims them."""

    def _account(self, db):
        account = PlatformAccount(platform="youtube", display_name="Canal", niche="geral")
        db.add(account)
        db.flush()
        return account

    def test_stale_queued_job_is_redispatched(self) -> None:
        db, Session = _make_session()
        account = self._account(db)
        job = VideoJob(
            title="Preso", mode="from_ready_video", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status=JobStatus.QUEUED,
            scheduled_at=datetime.utcnow() - timedelta(hours=2),
            updated_at=datetime.utcnow() - timedelta(minutes=30),
        )
        db.add(job)
        db.commit()
        job_id = job.id

        with patch.object(scheduler, "SessionLocal", Session), \
             patch("backend.pipeline.dispatch.is_inflight", return_value=False), \
             patch("backend.pipeline.dispatch.dispatch_job") as mock_dispatch:
            scheduler._job_recover_stuck_queued()

        mock_dispatch.assert_called_once_with(job_id)
        refreshed = Session().get(VideoJob, job_id)
        # The atomic claim itself flips QUEUED -> PROCESSING (see scheduler.py);
        # with dispatch_job mocked out, nothing moves it further from here.
        self.assertEqual(refreshed.status, JobStatus.PROCESSING)

    def test_freshly_queued_job_is_left_alone(self) -> None:
        """A job queued moments ago hasn't had a chance to be picked up by a
        normal worker loop yet — must not race with it."""
        db, Session = _make_session()
        account = self._account(db)
        job = VideoJob(
            title="Recem-enfileirado", mode="from_ready_video", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status=JobStatus.QUEUED,
            scheduled_at=datetime.utcnow() - timedelta(minutes=5),
            updated_at=datetime.utcnow() - timedelta(minutes=1),
        )
        db.add(job)
        db.commit()

        with patch.object(scheduler, "SessionLocal", Session), \
             patch("backend.pipeline.dispatch.is_inflight", return_value=False), \
             patch("backend.pipeline.dispatch.dispatch_job") as mock_dispatch:
            scheduler._job_recover_stuck_queued()

        mock_dispatch.assert_not_called()

    def test_inflight_job_is_never_redispatched(self) -> None:
        db, Session = _make_session()
        account = self._account(db)
        job = VideoJob(
            title="Em andamento em outro replica", mode="from_ready_video",
            content_type="film_recap_ai_images", video_format="long", account_id=account.id,
            status=JobStatus.QUEUED,
            scheduled_at=datetime.utcnow() - timedelta(hours=2),
            updated_at=datetime.utcnow() - timedelta(minutes=30),
        )
        db.add(job)
        db.commit()

        with patch.object(scheduler, "SessionLocal", Session), \
             patch("backend.pipeline.dispatch.is_inflight", return_value=True), \
             patch("backend.pipeline.dispatch.dispatch_job") as mock_dispatch:
            scheduler._job_recover_stuck_queued()

        mock_dispatch.assert_not_called()

    def test_batch_is_capped(self) -> None:
        db, Session = _make_session()
        account = self._account(db)
        total = scheduler._STUCK_QUEUED_BATCH_LIMIT + 4
        for i in range(total):
            db.add(VideoJob(
                title=f"Preso {i}", mode="from_ready_video", content_type="film_recap_ai_images",
                video_format="long", account_id=account.id, status=JobStatus.QUEUED,
                scheduled_at=datetime.utcnow() - timedelta(hours=2),
                updated_at=datetime.utcnow() - timedelta(minutes=30),
            ))
        db.commit()

        with patch.object(scheduler, "SessionLocal", Session), \
             patch("backend.pipeline.dispatch.is_inflight", return_value=False), \
             patch("backend.pipeline.dispatch.dispatch_job") as mock_dispatch:
            scheduler._job_recover_stuck_queued()

        self.assertEqual(mock_dispatch.call_count, scheduler._STUCK_QUEUED_BATCH_LIMIT)

    def test_job_with_slot_expired_over_24h_is_marked_error_not_redispatched(self) -> None:
        """Silently catching up days of missed slots would dump a burst of
        stale content onto the channel all at once — must stop and let a
        human decide instead."""
        db, Session = _make_session()
        account = self._account(db)
        ready = ReadyVideo(
            drive_file_id="abc123", name="track.mp3", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status="reserved",
        )
        db.add(ready)
        db.flush()
        job = VideoJob(
            title="Vencido ha dias", mode="from_ready_video", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status=JobStatus.QUEUED,
            scheduled_at=datetime.utcnow() - timedelta(hours=48),
            updated_at=datetime.utcnow() - timedelta(minutes=30),
            video_context={"source": "drive_ready_video", "ready_video_id": ready.id},
        )
        db.add(job)
        db.flush()
        ready.reserved_job_id = job.id
        db.commit()
        job_id = job.id
        ready_id = ready.id

        with patch.object(scheduler, "SessionLocal", Session), \
             patch("backend.pipeline.dispatch.is_inflight", return_value=False), \
             patch("backend.pipeline.dispatch.dispatch_job") as mock_dispatch:
            scheduler._job_recover_stuck_queued()

        mock_dispatch.assert_not_called()
        fresh_db = Session()
        refreshed_job = fresh_db.get(VideoJob, job_id)
        refreshed_ready = fresh_db.get(ReadyVideo, ready_id)
        self.assertEqual(refreshed_job.status, JobStatus.ERROR)
        self.assertIn("24h", refreshed_job.error_message)
        # The Drive file it was holding must return to the pool for another job.
        self.assertEqual(refreshed_ready.status, "available")
        self.assertIsNone(refreshed_ready.reserved_job_id)


class _BarrierSession(_Session):
    """Session that pauses right after the candidate-selecting SELECT so two
    "processes" can be made to race past that point before either claims,
    reproducing two concurrent scheduler ticks/replicas racing the same
    stuck-QUEUED candidate."""

    _barrier: threading.Barrier | None = None

    def execute(self, statement, *args, **kwargs):  # noqa: D401
        result = super().execute(statement, *args, **kwargs)
        sql = str(statement).lower()
        if (
            "select" in sql
            and "video_jobs" in sql
            and not getattr(self, "_synced", False)
        ):
            self._synced = True
            if self._barrier is not None:
                self._barrier.wait(timeout=5)
        return result


class RecoverStuckQueuedRaceConditionTests(unittest.TestCase):
    def test_two_concurrent_ticks_do_not_double_dispatch_same_job(self) -> None:
        """Reproduces two scheduler replicas both running _job_recover_stuck_queued
        in the same window: both SELECT the same stale QUEUED job before either
        claims it. The atomic claim must flip status away from QUEUED (not just
        bump updated_at) so the loser's UPDATE ... WHERE status == QUEUED hits
        rowcount == 0 and the job is only ever dispatched once."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_url = f"sqlite:///{tmp.name}"

        engine = create_engine(db_url, connect_args={"timeout": 30})

        @event.listens_for(engine, "connect")
        def _set_busy_timeout(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA busy_timeout=30000")

        Base.metadata.create_all(bind=engine)

        setup_session = sessionmaker(bind=engine)()
        account = PlatformAccount(platform="youtube", display_name="Canal Teste", niche="geral")
        setup_session.add(account)
        setup_session.flush()
        job = VideoJob(
            title="Preso", mode="from_ready_video", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status=JobStatus.QUEUED,
            scheduled_at=datetime.utcnow() - timedelta(hours=2),
            updated_at=datetime.utcnow() - timedelta(minutes=30),
        )
        setup_session.add(job)
        setup_session.commit()
        job_id = job.id
        setup_session.close()

        barrier = threading.Barrier(2, timeout=5)

        def session_factory():
            engine_thread = create_engine(db_url, connect_args={"timeout": 30})

            @event.listens_for(engine_thread, "connect")
            def _set_busy_timeout_thread(dbapi_conn, _):
                dbapi_conn.execute("PRAGMA busy_timeout=30000")

            Maker = sessionmaker(bind=engine_thread, class_=_BarrierSession)
            s = Maker()
            s._barrier = barrier
            return s

        dispatched: list[int] = []
        dispatch_lock = threading.Lock()

        def fake_dispatch_job(jid):
            with dispatch_lock:
                dispatched.append(jid)

        errors: list[Exception] = []

        def run():
            try:
                scheduler._job_recover_stuck_queued()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with patch.object(scheduler, "SessionLocal", session_factory), \
             patch("backend.pipeline.dispatch.is_inflight", return_value=False), \
             patch("backend.pipeline.dispatch.dispatch_job", side_effect=fake_dispatch_job):
            t1 = threading.Thread(target=run)
            t2 = threading.Thread(target=run)
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

        self.assertEqual(errors, [])
        self.assertEqual(dispatched.count(job_id), 1)

        verify_session = sessionmaker(bind=engine)()
        refreshed = verify_session.get(VideoJob, job_id)
        self.assertEqual(refreshed.status, JobStatus.PROCESSING)
        verify_session.close()


class RedispatchQueuedJobsBootTests(unittest.TestCase):
    """In Celery mode, from_ready_video/from_manual_upload jobs used to be
    silently skipped at boot (Celery never owns them, so nothing else would
    ever pick them up either) — this is a separate bug from the periodic
    sweep above, caught at a different point (process startup)."""

    def _make_session(self):
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine, future=True)
        return Session()

    def test_celery_mode_still_redispatches_ready_video_and_manual_upload_jobs(self) -> None:
        from backend import main as main_module

        db = self._make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal", niche="geral")
        db.add(account)
        db.flush()
        ready_video_job = VideoJob(
            title="Drive job", mode="from_ready_video", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status=JobStatus.QUEUED,
        )
        manual_job = VideoJob(
            title="Manual job", mode="from_manual_upload", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status=JobStatus.QUEUED,
        )
        generic_ai_job = VideoJob(
            title="AI job", mode="from_title", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status=JobStatus.QUEUED,
        )
        db.add_all([ready_video_job, manual_job, generic_ai_job])
        db.commit()
        expected_ids = {ready_video_job.id, manual_job.id}

        with patch.object(main_module.settings, "use_celery", True), \
             patch.object(main_module, "_safe_boot", return_value=False), \
             patch("backend.database.SessionLocal", return_value=db), \
             patch("backend.pipeline.dispatch.dispatch_job") as mock_dispatch:
            main_module._redispatch_queued_jobs()

        dispatched_ids = {call.args[0] for call in mock_dispatch.call_args_list}
        self.assertEqual(dispatched_ids, expected_ids)

    def test_non_celery_mode_redispatches_every_queued_job(self) -> None:
        from backend import main as main_module

        db = self._make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal", niche="geral")
        db.add(account)
        db.flush()
        generic_ai_job = VideoJob(
            title="AI job", mode="from_title", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status=JobStatus.QUEUED,
        )
        db.add(generic_ai_job)
        db.commit()
        job_id = generic_ai_job.id

        with patch.object(main_module.settings, "use_celery", False), \
             patch.object(main_module, "_safe_boot", return_value=False), \
             patch("backend.database.SessionLocal", return_value=db), \
             patch("backend.pipeline.dispatch.dispatch_job") as mock_dispatch:
            main_module._redispatch_queued_jobs()

        mock_dispatch.assert_called_once_with(job_id)


if __name__ == "__main__":
    unittest.main()
