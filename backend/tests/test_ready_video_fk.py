from __future__ import annotations

import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import ReadyVideo, VideoJob


class ReadyVideoJobForeignKeyTests(unittest.TestCase):
    """Guards against orphaned reserved_job_id/used_job_id references.

    reserved_job_id/used_job_id must be real FKs to video_jobs.id (with
    ondelete=SET NULL) so that deleting a VideoJob can't leave a ReadyVideo
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

    def test_deleting_video_job_clears_reserved_and_used_job_id(self) -> None:
        with self.Session() as db:
            job = VideoJob(title="Some job")
            db.add(job)
            db.commit()

            video = ReadyVideo(
                drive_file_id="drive-1",
                name="clip.mp4",
                status="used",
                reserved_job_id=job.id,
                used_job_id=job.id,
            )
            db.add(video)
            db.commit()

            db.delete(job)
            db.commit()

            db.refresh(video)
            self.assertIsNone(video.reserved_job_id)
            self.assertIsNone(video.used_job_id)


if __name__ == "__main__":
    unittest.main()
