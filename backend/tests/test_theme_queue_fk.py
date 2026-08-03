from __future__ import annotations

import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import ThemeQueue, VideoJob


class ThemeQueueJobForeignKeyTests(unittest.TestCase):
    """Guards against an orphaned consumed_job_id reference.

    consumed_job_id must be a real FK to video_jobs.id (with ondelete=SET
    NULL) so that deleting a VideoJob can't leave a ThemeQueue row
    permanently stuck referencing a non-existent job.
    """

    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", future=True)

        # SQLite ignores FK constraints unless explicitly enabled per-connection.
        @event.listens_for(self.engine, "connect")
        def _enable_fk(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True)

    def test_deleting_video_job_clears_consumed_job_id(self) -> None:
        with self.Session() as db:
            job = VideoJob(title="Some job")
            db.add(job)
            db.commit()

            theme = ThemeQueue(theme="A theme", status="consumed", consumed_job_id=job.id)
            db.add(theme)
            db.commit()

            db.delete(job)
            db.commit()

            db.refresh(theme)
            self.assertIsNone(theme.consumed_job_id)


if __name__ == "__main__":
    unittest.main()
