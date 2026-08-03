from __future__ import annotations

import tempfile
import threading
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend import scheduler
from backend.database import Base
from backend.models import JobStatus, PlatformAccount, VideoJob


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session_ = sessionmaker(bind=engine, future=True)
    return Session_(), Session_


class JobQuotaResetHappyPathTests(unittest.TestCase):
    def test_quota_exceeded_accounts_are_reactivated_and_awaiting_jobs_resumed(self) -> None:
        db, Session_ = _make_session()
        account = PlatformAccount(
            platform="youtube", display_name="Canal", niche="geral",
            status="quota_exceeded", quota_used_today=5,
        )
        db.add(account)
        db.flush()
        held = VideoJob(
            title="Preso na quota", mode="from_title", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status=JobStatus.AWAITING_QUOTA,
        )
        unrelated = VideoJob(
            title="Nao afetado", mode="from_title", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status=JobStatus.QUEUED,
        )
        db.add_all([held, unrelated])
        db.commit()
        held_id, unrelated_id, account_id = held.id, unrelated.id, account.id

        with patch.object(scheduler, "SessionLocal", Session_), \
             patch("backend.pipeline.dispatch.dispatch_publish") as fake_dispatch:
            scheduler._job_quota_reset()

        fake_dispatch.assert_called_once_with(held_id)

        verify = Session_()
        refreshed_account = verify.get(PlatformAccount, account_id)
        refreshed_held = verify.get(VideoJob, held_id)
        refreshed_unrelated = verify.get(VideoJob, unrelated_id)
        verify.close()

        self.assertEqual(refreshed_account.status, "active")
        self.assertEqual(refreshed_account.quota_used_today, 0)
        self.assertEqual(refreshed_held.status, JobStatus.APPROVED)
        self.assertEqual(refreshed_held.approval_status, "approved")
        # A job that was never AWAITING_QUOTA must be left untouched.
        self.assertEqual(refreshed_unrelated.status, JobStatus.QUEUED)

    def test_no_awaiting_quota_jobs_is_a_clean_noop(self) -> None:
        db, Session_ = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal", niche="geral")
        db.add(account)
        db.commit()

        with patch.object(scheduler, "SessionLocal", Session_), \
             patch("backend.pipeline.dispatch.dispatch_publish") as fake_dispatch:
            scheduler._job_quota_reset()

        fake_dispatch.assert_not_called()


class _BarrierSession(Session):
    """Session that pauses right after the AWAITING_QUOTA id SELECT so two
    "processes" can race past that point before either claims a job."""

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


class JobQuotaResetRaceConditionTests(unittest.TestCase):
    def test_two_concurrent_ticks_do_not_double_dispatch_same_job(self) -> None:
        """Reproduces two scheduler replicas both running _job_quota_reset in
        the same window: both SELECT the same AWAITING_QUOTA job before
        either claims it. The atomic UPDATE ... WHERE status == AWAITING_QUOTA
        guard must let only one of them win."""
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
            title="Preso na quota", mode="from_title", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status=JobStatus.AWAITING_QUOTA,
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

        def fake_dispatch_publish(jid):
            with dispatch_lock:
                dispatched.append(jid)

        errors: list[Exception] = []

        def run():
            try:
                scheduler._job_quota_reset()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with patch.object(scheduler, "SessionLocal", session_factory), \
             patch("backend.pipeline.dispatch.dispatch_publish", side_effect=fake_dispatch_publish):
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
        self.assertEqual(refreshed.status, JobStatus.APPROVED)
        verify_session.close()


if __name__ == "__main__":
    unittest.main()
