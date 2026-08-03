from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta
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


class JobPublishDueHappyPathTests(unittest.TestCase):
    def test_due_approved_job_is_claimed_and_dispatched(self) -> None:
        db, Session_ = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal", niche="geral")
        db.add(account)
        db.flush()
        due_job = VideoJob(
            title="No horario", mode="from_title", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status=JobStatus.APPROVED,
            scheduled_at=datetime.utcnow() - timedelta(minutes=1),
        )
        future_job = VideoJob(
            title="Ainda nao chegou", mode="from_title", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status=JobStatus.APPROVED,
            scheduled_at=datetime.utcnow() + timedelta(hours=1),
        )
        pending_job = VideoJob(
            title="Aguardando aprovacao", mode="from_title", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status=JobStatus.AWAITING_APPROVAL,
            scheduled_at=datetime.utcnow() - timedelta(minutes=1),
        )
        db.add_all([due_job, future_job, pending_job])
        db.commit()
        due_id, future_id, pending_id = due_job.id, future_job.id, pending_job.id

        with patch.object(scheduler, "SessionLocal", Session_), \
             patch("backend.pipeline.dispatch.dispatch_publish") as fake_dispatch:
            scheduler._job_publish_due()

        fake_dispatch.assert_called_once_with(due_id)

        verify = Session_()
        self.assertEqual(verify.get(VideoJob, due_id).status, JobStatus.PUBLISHING)
        # A future slot and a human-gated job must never be touched.
        self.assertEqual(verify.get(VideoJob, future_id).status, JobStatus.APPROVED)
        self.assertEqual(verify.get(VideoJob, pending_id).status, JobStatus.AWAITING_APPROVAL)
        verify.close()

    def test_no_due_jobs_is_a_clean_noop(self) -> None:
        db, Session_ = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal", niche="geral")
        db.add(account)
        db.commit()

        with patch.object(scheduler, "SessionLocal", Session_), \
             patch("backend.pipeline.dispatch.dispatch_publish") as fake_dispatch:
            scheduler._job_publish_due()

        fake_dispatch.assert_not_called()


class _BarrierSession(Session):
    """Session that pauses right after the due-jobs SELECT so two "processes"
    can race past that point before either claims the same job."""

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


class JobPublishDueRaceConditionTests(unittest.TestCase):
    def test_two_concurrent_ticks_do_not_double_publish_same_job(self) -> None:
        """Reproduces two scheduler replicas both running _job_publish_due in
        the same window: both SELECT the same due APPROVED job before either
        claims it. The atomic UPDATE ... WHERE status == APPROVED guard must
        let only one of them win, so the video is never published twice."""
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
            title="No horario", mode="from_title", content_type="film_recap_ai_images",
            video_format="long", account_id=account.id, status=JobStatus.APPROVED,
            scheduled_at=datetime.utcnow() - timedelta(minutes=1),
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
                scheduler._job_publish_due()
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
        self.assertEqual(refreshed.status, JobStatus.PUBLISHING)
        verify_session.close()


if __name__ == "__main__":
    unittest.main()
