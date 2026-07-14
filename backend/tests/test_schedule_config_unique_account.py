"""Regression test: PUT /schedule/config/{account_id} must never create two
ScheduleConfig rows for the same account, even under a race (two concurrent
requests that both see "no existing row" before either commits)."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import backend.models  # noqa: F401  (registers all mappers on Base.metadata)
from backend.database import Base
from backend.models import PlatformAccount, ScheduleConfig
from backend.routers.schedule import ScheduleConfigIn
from backend.routers.schedule import upsert_config as upsert_config_endpoint


class ScheduleConfigUniqueAccountTests(unittest.TestCase):
    def setUp(self) -> None:
        # A real file-backed SQLite DB (not :memory:) so each thread gets its own
        # pooled connection with proper file locking — genuine concurrent access,
        # like separate request connections against Postgres in production.
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tmpdir.name) / "test.db"
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 5},
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def tearDown(self) -> None:
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()
        self._tmpdir.cleanup()

    def test_concurrent_upserts_do_not_duplicate_config_row(self) -> None:
        setup_db = self.SessionLocal()
        try:
            account = PlatformAccount(platform="youtube", display_name="Channel 0")
            setup_db.add(account)
            setup_db.commit()
            account_id = account.id
        finally:
            setup_db.close()

        payload = ScheduleConfigIn(mode="fixed", videos_per_day=2, post_times=["19:00"])
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def _run() -> None:
            db = self.SessionLocal()
            try:
                barrier.wait(timeout=5)  # force both PUTs to race for real
                upsert_config_endpoint(account_id, payload, db=db)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                db.close()

        threads = [threading.Thread(target=_run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertFalse(errors, f"upsert_config raised under concurrency: {errors}")

        verify_db = self.SessionLocal()
        try:
            rows = verify_db.execute(
                select(ScheduleConfig).where(ScheduleConfig.account_id == account_id)
            ).scalars().all()
            self.assertEqual(len(rows), 1, "duplicate ScheduleConfig rows created for one account")
        finally:
            verify_db.close()


if __name__ == "__main__":
    unittest.main()
