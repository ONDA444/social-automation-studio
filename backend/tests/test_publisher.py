from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agents.publisher import _with_retry, run_publish
from backend.database import Base
from backend.models import JobStatus, VideoJob


def _make_sessionmaker():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, future=True)


class RunPublishIdempotencyTests(unittest.TestCase):
    def test_already_published_job_is_not_reuploaded_to_youtube(self) -> None:
        """The idempotency guard (publisher.py ~104-117) must short-circuit
        BEFORE any upload is attempted when the job already has a recorded
        YouTube video_id for every target platform. A regression here would
        re-upload the same video to YouTube on every stray retry/restart."""
        Session = _make_sessionmaker()
        db = Session()
        job = VideoJob(
            title="Video ja publicado",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            status=JobStatus.APPROVED,
            target_platforms=["youtube"],
            publish_status={"youtube": {"ok": True, "video_id": "abc123", "status": "ok"}},
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id
        db.close()

        with patch("backend.agents.publisher.SessionLocal", Session), \
             patch("backend.agents.publisher.publish_youtube") as mock_publish_yt:
            result = asyncio.run(run_publish(job_id))

        mock_publish_yt.assert_not_called()
        self.assertEqual(result["status"], JobStatus.PUBLISHED.value)
        self.assertIn("já publicado", result.get("note", ""))

        db2 = Session()
        refreshed = db2.get(VideoJob, job_id)
        self.assertEqual(refreshed.status, JobStatus.PUBLISHED)
        db2.close()


class WithRetryTerminalStatusTests(unittest.TestCase):
    def test_terminal_status_is_not_retried(self) -> None:
        """quota_exceeded (and the other terminal states listed in _with_retry)
        must return immediately after the first attempt instead of sleeping
        through the backoff and retrying — retrying a terminal failure can
        never succeed and just occupies a worker slot."""
        calls = {"n": 0}

        def fake_upload(*args, **kwargs):
            calls["n"] += 1
            return {"ok": False, "status": "quota_exceeded", "error": "quota atingida"}

        with patch("backend.agents.publisher.asyncio.sleep") as mock_sleep:
            result = asyncio.run(_with_retry(fake_upload, label="test"))

        self.assertEqual(calls["n"], 1)
        mock_sleep.assert_not_called()
        self.assertEqual(result["status"], "quota_exceeded")

    def test_transient_failure_is_retried_up_to_three_times(self) -> None:
        """A non-terminal failure (e.g. a transient network error) must be
        retried, unlike the terminal statuses above."""
        calls = {"n": 0}

        def fake_upload(*args, **kwargs):
            calls["n"] += 1
            return {"ok": False, "status": "error", "error": "temporary"}

        with patch("backend.agents.publisher.asyncio.sleep") as mock_sleep:
            result = asyncio.run(_with_retry(fake_upload, label="test"))

        self.assertEqual(calls["n"], 3)
        self.assertEqual(mock_sleep.call_count, 2)
        self.assertEqual(result["status"], "error")


if __name__ == "__main__":
    unittest.main()
