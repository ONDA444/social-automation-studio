from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.main import _apply_orphan_transition
from backend.models import JobStatus, VideoJob


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


class OrphanResumeIndependentOfRetryCountTests(unittest.TestCase):
    def test_orphan_resume_still_happens_after_llm_retry_bumped_retry_count(self) -> None:
        """A job that already had 1 LLM-failure retry (retry_count=1, via
        scheduler._job_resurrect_llm_failures) must still get its ONE orphan
        resume when its render later dies mid-PROCESSING — retry_count and the
        orphan-resume budget must not share the same counter."""
        db = _make_session()

        job = VideoJob(
            title="Video travado",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            status=JobStatus.PROCESSING,
            retry_count=1,  # already consumed by an unrelated LLM-failure retry
        )
        db.add(job)
        db.commit()

        _apply_orphan_transition(job, safe=False)
        db.commit()

        self.assertEqual(job.status, JobStatus.QUEUED)
        self.assertIsNone(job.error_message)
        self.assertEqual(job.orphan_resume_count, 1)

        # A second orphan (this resume died too) parks it for manual Retry.
        job.status = JobStatus.PROCESSING
        _apply_orphan_transition(job, safe=False)
        db.commit()

        self.assertEqual(job.status, JobStatus.ERROR)


if __name__ == "__main__":
    unittest.main()
