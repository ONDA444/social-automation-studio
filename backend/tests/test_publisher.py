from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agents.publisher import _with_retry, publish_youtube, run_publish
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


class ShortsTitleSuffixSurvivesTruncationTests(unittest.TestCase):
    """Regression test: seo_agent's _clamp() already caps the title at 100
    chars with no margin reserved. Appending " #Shorts" and THEN slicing to
    100 either dropped the suffix entirely (title already =100 chars) or cut
    it mid-word (title >92 chars) — silently losing the exact signal the
    Shorts classifier needs, on precisely the titles it needs it most."""

    def setUp(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.write(b"fake video bytes")
        tmp.close()
        self.video_path = tmp.name
        self.addCleanup(lambda: Path(self.video_path).unlink(missing_ok=True))

    def _publish(self, title: str, video_format: str = "short", shorts: list | None = None):
        job = SimpleNamespace(
            id=1, main_video_path=self.video_path, video_format=video_format,
            thumbnail_path=None, title="fallback",
        )
        seo = {"youtube": {"title": title, "description": "", "tags": [], "category_id": "22"}}
        captured = {}

        async def fake_with_retry(fn, *args, **kwargs):
            if fn.__name__ == "upload_video" and "main_title" not in captured:
                captured["main_title"] = args[1]
            return {"ok": True, "video_id": "vid1"}

        with patch("backend.agents.publisher._with_retry", side_effect=fake_with_retry):
            asyncio.run(publish_youtube(
                job, seo, creds=None, publish_at=None, shorts=shorts or [],
            ))
        return captured["main_title"]

    def test_title_already_at_100_chars_still_gets_the_shorts_suffix(self) -> None:
        title = "A" * 100
        result = self._publish(title)
        self.assertTrue(result.endswith(" #Shorts"), result)
        self.assertLessEqual(len(result), 100)

    def test_title_near_the_limit_is_not_cut_mid_word(self) -> None:
        title = "A" * 95
        result = self._publish(title)
        self.assertTrue(result.endswith(" #Shorts"), result)
        self.assertLessEqual(len(result), 100)

    def test_short_title_is_unaffected(self) -> None:
        result = self._publish("Titulo curto")
        self.assertEqual(result, "Titulo curto #Shorts")

    def test_title_already_tagged_is_not_double_suffixed(self) -> None:
        result = self._publish("Titulo com #Shorts ja incluso")
        self.assertEqual(result.count("#Shorts"), 1)


if __name__ == "__main__":
    unittest.main()
