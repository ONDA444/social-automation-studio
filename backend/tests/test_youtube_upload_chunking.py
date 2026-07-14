from __future__ import annotations

import unittest
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


if __name__ == "__main__":
    unittest.main()
