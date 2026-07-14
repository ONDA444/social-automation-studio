from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from backend.uploaders import tiktok


class TikTokUploadStatusTests(unittest.TestCase):
    def test_upload_video_detects_post_upload_failure(self) -> None:
        """If TikTok accepts the PUT but later reports FAILED processing,
        upload_video() must NOT report status='published'."""

        def fake_post(url, json=None, headers=None, timeout=None):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            if url == tiktok.INIT_URL:
                resp.json.return_value = {
                    "data": {
                        "publish_id": "pub123",
                        "upload_url": "https://upload.example/put",
                    }
                }
            elif url == tiktok.STATUS_URL:
                resp.json.return_value = {"data": {"status": "FAILED", "fail_reason": "video_format_check_failed"}}
            return resp

        def fake_put(url, content=None, headers=None, timeout=None):
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            return resp

        with patch.object(tiktok.settings, "tiktok_client_key", "key"), \
             patch.object(tiktok.settings, "tiktok_client_secret", "secret"), \
             patch.object(tiktok, "httpx") as mock_httpx, \
             patch("os.path.getsize", return_value=100), \
             patch("builtins.open", MagicMock()), \
             patch("time.sleep", return_value=None):
            mock_httpx.post.side_effect = fake_post
            mock_httpx.put.side_effect = fake_put
            mock_httpx.HTTPStatusError = Exception

            result = tiktok.upload_video("video.mp4", "caption", {"access_token": "tok"})

        self.assertFalse(result["ok"])
        self.assertNotEqual(result["status"], "published")
        self.assertIn("video_format_check_failed", result["error"])


if __name__ == "__main__":
    unittest.main()
