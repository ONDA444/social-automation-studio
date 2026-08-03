from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.database import Base
from backend.models import JobStatus, PlatformAccount, VideoJob
from backend import scheduler


class _BarrierSession(Session):
    """Session that pauses right after the job-selecting SELECT so two
    "processes" can be made to race past that point before either commits,
    reproducing the concurrent-retry scenario."""

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


class RetryErroredRaceConditionTests(unittest.TestCase):
    def test_two_concurrent_ticks_do_not_double_dispatch_same_job(self) -> None:
        """Reproduces two scheduler replicas both running _job_retry_errored in
        the same window: both SELECT the same ERROR job before either commits.
        Without an atomic claim, both would flip the job to QUEUED and call
        dispatch_job for the same id (duplicate render/publish). With the
        atomic UPDATE ... WHERE status == ERROR guard, only one may claim it.
        """
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
            title="Video com erro",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.ERROR,
            retry_count=0,
            error_message="429 rate limit",
            updated_at=datetime.utcnow() - timedelta(hours=2),
        )
        setup_session.add(job)
        setup_session.commit()
        job_id = job.id
        setup_session.close()

        barrier = threading.Barrier(2, timeout=5)

        def session_factory():
            # Each "replica" gets its OWN connection to the same file-backed DB
            # (mirrors two separate processes), synchronized via the barrier so
            # both SELECTs land before either UPDATE/commit — the exact race.
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

        settings_mock = type("S", (), {"llm_retry_max": 5})()

        errors: list[Exception] = []

        def run():
            try:
                scheduler._job_retry_errored()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with patch.object(scheduler, "SessionLocal", session_factory), \
             patch("backend.config.settings", settings_mock), \
             patch("backend.pipeline.dispatch.dispatch_job", side_effect=fake_dispatch_job):
            t1 = threading.Thread(target=run)
            t2 = threading.Thread(target=run)
            t1.start()
            t2.start()
            t1.join(timeout=15)
            t2.join(timeout=15)

        self.assertEqual(errors, [])
        # The critical assertion: the same job must only ever be dispatched once,
        # even though both replicas raced past the initial SELECT together.
        self.assertEqual(dispatched.count(job_id), 1)

        verify_session = sessionmaker(bind=engine)()
        refreshed = verify_session.get(VideoJob, job_id)
        self.assertEqual(refreshed.status, JobStatus.QUEUED)
        self.assertEqual(refreshed.retry_count, 1)
        verify_session.close()


class RetryErroredBatchLimitTests(unittest.TestCase):
    """A mass LLM outage can park dozens of jobs in ERROR whose backoffs then
    reopen on the same tick — _job_retry_errored must cap how many it
    dispatches per tick instead of resurrecting the whole batch at once."""

    def _make_engine(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_url = f"sqlite:///{tmp.name}"
        engine = create_engine(db_url, connect_args={"timeout": 30})
        Base.metadata.create_all(bind=engine)
        return engine

    def test_dispatches_are_capped_per_tick(self) -> None:
        engine = self._make_engine()
        Session_ = sessionmaker(bind=engine)
        setup = Session_()
        account = PlatformAccount(platform="youtube", display_name="Canal Teste", niche="geral")
        setup.add(account)
        setup.flush()

        total = scheduler._RETRY_ERRORED_BATCH_LIMIT + 4
        for i in range(total):
            setup.add(VideoJob(
                title=f"Falha {i}", mode="from_title", content_type="film_recap_ai_images",
                video_format="long", account_id=account.id, status=JobStatus.ERROR,
                retry_count=0, error_message="429 rate limit",
                updated_at=datetime.utcnow() - timedelta(hours=2),
            ))
        setup.commit()
        setup.close()

        dispatched: list[int] = []
        settings_mock = type("S", (), {"llm_retry_max": 5})()

        with patch.object(scheduler, "SessionLocal", sessionmaker(bind=engine)), \
             patch("backend.config.settings", settings_mock), \
             patch("backend.pipeline.dispatch.dispatch_job", side_effect=dispatched.append):
            scheduler._job_retry_errored()

        self.assertEqual(len(dispatched), scheduler._RETRY_ERRORED_BATCH_LIMIT)

        verify = Session_()
        still_errored = verify.query(VideoJob).filter(VideoJob.status == JobStatus.ERROR).count()
        verify.close()
        self.assertEqual(still_errored, total - scheduler._RETRY_ERRORED_BATCH_LIMIT)


class RetryErroredMarkerExclusionTests(unittest.TestCase):
    """Verifies _job_retry_errored() actually excludes jobs whose error_message
    matches a _NO_AUTO_RETRY_MARKERS entry, and still resurrects eligible ones.
    A regression here (e.g. exact-match instead of substring, or case handling)
    would either infinite-loop dead-credential jobs or strand retryable ones."""

    def _make_engine(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_url = f"sqlite:///{tmp.name}"
        engine = create_engine(db_url, connect_args={"timeout": 30})
        Base.metadata.create_all(bind=engine)
        return engine

    def test_marker_matched_jobs_are_skipped_others_are_requeued(self) -> None:
        engine = self._make_engine()
        Session_ = sessionmaker(bind=engine)
        setup = Session_()
        account = PlatformAccount(platform="youtube", display_name="Canal Teste", niche="geral")
        setup.add(account)
        setup.flush()

        blocked = VideoJob(
            title="Auth morta",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.ERROR,
            retry_count=0,
            error_message="OAuth error: invalid_grant, token expired or revoked",
            updated_at=datetime.utcnow() - timedelta(hours=2),
        )
        retryable = VideoJob(
            title="Falha transitoria",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=account.id,
            status=JobStatus.ERROR,
            retry_count=0,
            error_message="429 rate limit",
            updated_at=datetime.utcnow() - timedelta(hours=2),
        )
        setup.add_all([blocked, retryable])
        setup.commit()
        blocked_id, retryable_id = blocked.id, retryable.id
        setup.close()

        dispatched: list[int] = []
        settings_mock = type("S", (), {"llm_retry_max": 5})()

        with patch.object(scheduler, "SessionLocal", sessionmaker(bind=engine)), \
             patch("backend.config.settings", settings_mock), \
             patch("backend.pipeline.dispatch.dispatch_job", side_effect=dispatched.append):
            scheduler._job_retry_errored()

        verify = Session_()
        blocked_refreshed = verify.get(VideoJob, blocked_id)
        retryable_refreshed = verify.get(VideoJob, retryable_id)
        verify.close()

        # Marker-matched job must stay parked in ERROR, untouched, never dispatched.
        self.assertEqual(blocked_refreshed.status, JobStatus.ERROR)
        self.assertEqual(blocked_refreshed.retry_count, 0)
        self.assertNotIn(blocked_id, dispatched)

        # Non-matching job must be requeued and re-dispatched.
        self.assertEqual(retryable_refreshed.status, JobStatus.QUEUED)
        self.assertEqual(retryable_refreshed.retry_count, 1)
        self.assertIn(retryable_id, dispatched)


if __name__ == "__main__":
    unittest.main()
