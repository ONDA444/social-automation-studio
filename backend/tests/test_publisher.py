from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agents.publisher import (
    _overall_status,
    _resolve_account,
    _with_retry,
    publish_youtube,
    run_publish,
)
from backend.crypto import encrypt_credentials
from backend.database import Base
from backend.models import JobStatus, PlatformAccount, VideoJob


def _make_sessionmaker():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, future=True)


def _make_file_sessionmaker(tmpdir: tempfile.TemporaryDirectory):
    # run_publish reaches into a worker thread (the heartbeat and
    # _ensure_ready_video_local both open their own SessionLocal()). A plain
    # sqlite:///:memory: engine hands each thread a SEPARATE, empty database
    # (SingletonThreadPool) — a real file-backed DB is required so every
    # thread sees the same rows, like separate connections against Postgres
    # in production.
    db_path = Path(tmpdir.name) / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, future=True)


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


class MultiPlatformPartialFailureTests(unittest.TestCase):
    def test_youtube_success_with_tiktok_failure_still_publishes_the_job(self) -> None:
        """_overall_status flips to PUBLISHED the moment ANY platform succeeds,
        even while another target platform failed — a 2-platform job must not
        be left in ERROR just because TikTok choked. error_message must name
        the failed platform so the user knows what didn't go out."""
        import threading

        tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmpdir.cleanup)
        engine, Session = _make_file_sessionmaker(tmpdir)
        self.addCleanup(engine.dispose)
        db = Session()
        yt_acct = PlatformAccount(
            platform="youtube", display_name="Canal YT", status="active",
            credentials_encrypted=encrypt_credentials({"refresh_token": "rt"}),
        )
        tk_acct = PlatformAccount(
            platform="tiktok", display_name="Conta TikTok", status="active",
            credentials_encrypted=encrypt_credentials({"access_token": "at"}),
        )
        db.add_all([yt_acct, tk_acct])
        job = VideoJob(
            title="Job multi-plataforma",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            status=JobStatus.APPROVED,
            target_platforms=["youtube", "tiktok"],
            main_video_path="/tmp/does-not-matter.mp4",
            shorts_paths=["/tmp/short_1.mp4"],
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id
        db.close()

        with patch("backend.agents.publisher.SessionLocal", Session), \
             patch("backend.agents.publisher.publish_youtube",
                   return_value={"ok": True, "platform": "youtube", "status": "ok", "video_id": "vid1"}), \
             patch("backend.agents.publisher.self_publish_tiktok",
                   return_value={"ok": False, "platform": "tiktok", "status": "error", "error": "boom"}):
            result = asyncio.run(run_publish(job_id))

        # The heartbeat thread's own SessionLocal keeps a connection open to the
        # file-backed DB above; join it before the tmpdir cleanup runs a Windows
        # file delete or it races an in-use handle.
        for t in threading.enumerate():
            if t.name == f"studio-publish-heartbeat-{job_id}":
                t.join(timeout=5)

        self.assertEqual(result["status"], JobStatus.PUBLISHED.value)
        self.assertTrue(result["results"]["youtube"]["ok"])
        self.assertFalse(result["results"]["tiktok"]["ok"])

        db2 = Session()
        refreshed = db2.get(VideoJob, job_id)
        self.assertEqual(refreshed.status, JobStatus.PUBLISHED)
        self.assertIn("tiktok", refreshed.error_message)
        self.assertTrue(refreshed.error_message.startswith("Publicado parcialmente"))
        db2.close()


class OverallStatusTests(unittest.TestCase):
    def test_any_success_wins_even_with_another_platform_failing(self) -> None:
        results = {
            "youtube": {"ok": True, "status": "ok"},
            "tiktok": {"ok": False, "status": "error"},
        }
        self.assertEqual(_overall_status(results), JobStatus.PUBLISHED)

    def test_no_success_with_quota_exceeded_is_awaiting_quota(self) -> None:
        results = {"youtube": {"ok": False, "status": "quota_exceeded"}}
        self.assertEqual(_overall_status(results), JobStatus.AWAITING_QUOTA)

    def test_no_success_with_tiktok_pending_is_tiktok_pending_approval(self) -> None:
        results = {"tiktok": {"ok": False, "status": "tiktok_pending_approval"}}
        self.assertEqual(_overall_status(results), JobStatus.TIKTOK_PENDING_APPROVAL)

    def test_no_success_falls_back_to_error(self) -> None:
        results = {"youtube": {"ok": False, "status": "auth_error"}}
        self.assertEqual(_overall_status(results), JobStatus.ERROR)


class ResolveAccountPinningTests(unittest.TestCase):
    """job.account_id must pin the publish to a SPECIFIC channel — otherwise
    a user with 2+ connected YouTube channels can't control which one a job
    goes out on, and it silently falls back to whichever has the most quota."""

    def test_pinned_account_wins_over_highest_quota_account(self) -> None:
        pinned = SimpleNamespace(id=1, platform="youtube")
        fallback = SimpleNamespace(id=2, platform="youtube")

        class FakeSvc:
            def has_valid_credentials(self, acct):
                return True

            def get_active_account(self, platform):
                return fallback

        self.assertIs(_resolve_account(FakeSvc(), "youtube", pinned), pinned)

    def test_falls_back_when_pin_is_for_a_different_platform(self) -> None:
        pinned = SimpleNamespace(id=1, platform="tiktok")
        fallback = SimpleNamespace(id=2, platform="youtube")

        class FakeSvc:
            def has_valid_credentials(self, acct):
                return True

            def get_active_account(self, platform):
                return fallback

        self.assertIs(_resolve_account(FakeSvc(), "youtube", pinned), fallback)

    def test_falls_back_when_pinned_account_has_no_valid_credentials(self) -> None:
        pinned = SimpleNamespace(id=1, platform="youtube")
        fallback = SimpleNamespace(id=2, platform="youtube")

        class FakeSvc:
            def has_valid_credentials(self, acct):
                return acct is fallback

            def get_active_account(self, platform):
                return fallback

        self.assertIs(_resolve_account(FakeSvc(), "youtube", pinned), fallback)


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
