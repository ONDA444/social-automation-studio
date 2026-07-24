from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.uploaders import youtube


class YoutubeUploadChunkingTests(unittest.TestCase):
    """Regression test for the bug where chunksize=-1 sends the whole file in a
    single next_chunk() call, defeating the wall-clock deadline that is supposed
    to protect the resumable-upload loop against a hung call."""

    def test_upload_video_uses_finite_chunksize_so_deadline_can_be_enforced(self) -> None:
        captured = {}

        class _FakeMediaFileUpload:
            def __init__(self, *args, **kwargs):
                captured["chunksize"] = kwargs.get("chunksize")

        fake_request = MagicMock()
        fake_request.next_chunk.return_value = (None, {"id": "vid123"})

        fake_yt = MagicMock()
        fake_yt.videos.return_value.insert.return_value = fake_request

        with patch.object(youtube, "_missing_libs", return_value=None), \
             patch.object(youtube, "_service", return_value=fake_yt), \
             patch("googleapiclient.http.MediaFileUpload", _FakeMediaFileUpload):
            result = youtube.upload_video(
                video_path="fake.mp4",
                title="t",
                description="d",
                tags=[],
                credentials={},
            )

        self.assertTrue(result.get("ok"))
        # The core assertion: chunksize must NOT be -1 (single-shot upload),
        # otherwise the deadline check between next_chunk() calls never runs.
        self.assertNotEqual(captured.get("chunksize"), -1)
        self.assertIsInstance(captured.get("chunksize"), int)
        self.assertGreater(captured.get("chunksize"), 0)


class YoutubeUploadStallDetectionTests(unittest.TestCase):
    """Regression test for the bug where a FIXED wall-clock deadline (25s) on
    the entire upload — not just a stall — aborted perfectly healthy, just
    slow-because-the-file-is-big uploads, misclassified them as auth_error,
    and let the auto-resurrection sweep re-render + re-upload a duplicate.
    The loop must only raise on genuine STALL (no forward progress), never
    on total elapsed time alone."""

    def _run_upload(self, next_chunk_results, monotonic_values):
        fake_request = MagicMock()
        fake_request.next_chunk.side_effect = next_chunk_results

        fake_yt = MagicMock()
        fake_yt.videos.return_value.insert.return_value = fake_request

        with patch.object(youtube, "_missing_libs", return_value=None), \
             patch.object(youtube, "_service", return_value=fake_yt), \
             patch("googleapiclient.http.MediaFileUpload"), \
             patch("time.monotonic", side_effect=monotonic_values):
            return youtube.upload_video(
                video_path="fake.mp4", title="t", description="d", tags=[], credentials={},
            )

    def test_slow_but_progressing_upload_succeeds_despite_long_elapsed_time(self) -> None:
        # Each next_chunk() call reports MORE bytes than the last, spanning far
        # longer than the old 25s cap — must NOT be treated as stalled.
        progress_1 = SimpleNamespace(resumable_progress=1_000_000)
        progress_2 = SimpleNamespace(resumable_progress=5_000_000)
        next_chunk_results = [
            (progress_1, None),
            (progress_2, None),
            (None, {"id": "vid123"}),
        ]
        # monotonic() calls: 1 initial (last_progress_at) + 1 per loop iteration (3 iters) = 4
        monotonic_values = [0, 40, 90, 140]  # 140s total — well past the old 25s bug
        result = self._run_upload(next_chunk_results, monotonic_values)
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("video_id"), "vid123")

    def test_genuinely_stalled_upload_with_zero_progress_raises_after_stall_window(self) -> None:
        # next_chunk() keeps returning the SAME progress (or None) forever —
        # the dead-token 401-refresh-storm this check exists to catch.
        stuck = SimpleNamespace(resumable_progress=0)
        next_chunk_results = [(stuck, None)] * 10
        # initial=0, then iterations at 10, 50, 130, ... (each +90s with zero
        # progress) — must raise once the gap exceeds _UPLOAD_STALL_TIMEOUT_S (120s).
        monotonic_values = [0] + [10 + 90 * i for i in range(10)]
        # upload_video() catches the TimeoutError internally and returns an
        # error dict rather than raising — assert on that classification.
        result = self._run_upload(next_chunk_results, monotonic_values)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("status"), "auth_error")
        self.assertIn("travado", result.get("error", "").lower())


class YoutubeServiceRedirectCodesTests(unittest.TestCase):
    """Regression test for the duplicate-upload bug confirmed live in
    production: YouTube's resumable-upload protocol repurposes HTTP 308 as
    "Resume Incomplete" (a normal mid-upload progress signal), but httplib2's
    default redirect_codes still includes 308 — without stripping it, a 308
    chunk response can raise httplib2.RedirectMissingLocation AFTER YouTube
    already accepted the bytes, and publisher.py's blind retry then
    re-uploaded the same file from scratch, producing 2-3 duplicate real
    videos from a single VideoJob. googleapiclient.http.build_http() strips
    308 for exactly this documented reason; _service() must replicate it
    since it builds its own httplib2.Http() for a custom timeout instead of
    using build_http()."""

    def test_service_strips_308_from_redirect_codes(self) -> None:
        creds = {"token": "x", "refresh_token": "y", "client_id": "a", "client_secret": "b"}
        svc = youtube._service(creds)
        redirect_codes = svc._http.http.redirect_codes
        self.assertNotIn(308, redirect_codes)
        # Real redirects must still be handled normally — this isn't a blanket
        # "ignore all redirects" hack, just the one code YouTube/Drive repurpose.
        self.assertIn(301, redirect_codes)
        self.assertIn(302, redirect_codes)


if __name__ == "__main__":
    unittest.main()
