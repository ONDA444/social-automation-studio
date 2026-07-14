from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from backend.uploaders import instagram


class InstagramUploadUsesPageTokenTest(unittest.TestCase):
    """upload_reel() must use the page token (not the raw user token) when
    credentials include one, since instagram_content_publish is authorized
    via the Page/IG token, not the plain user token."""

    def _mock_response(self, json_data, status_ok=True):
        resp = MagicMock()
        resp.json.return_value = json_data
        resp.raise_for_status = MagicMock()
        return resp

    @patch("backend.uploaders.instagram._to_public_url", return_value="https://example.com/v.mp4")
    @patch("backend.uploaders.instagram.settings")
    @patch("backend.uploaders.instagram.httpx")
    def test_uses_page_token_for_graph_calls(self, mock_httpx, mock_settings, _mock_public_url):
        mock_settings.meta_app_id = "app-id"
        mock_settings.meta_app_secret = "app-secret"

        mock_httpx.post.side_effect = [
            self._mock_response({"id": "container-1"}),
            self._mock_response({"id": "media-1"}),
        ]
        mock_httpx.get.return_value = self._mock_response({"status_code": "FINISHED"})

        credentials = {
            "access_token": "user-token",
            "page_token": "page-token",
            "ig_user_id": "ig-123",
        }

        result = instagram.upload_reel("video.mp4", "caption", credentials)

        self.assertTrue(result["ok"])

        # Every Graph API call must have used the page token, not the user token.
        for call in mock_httpx.post.call_args_list + mock_httpx.get.call_args_list:
            params = call.kwargs.get("params", {})
            self.assertEqual(params.get("access_token"), "page-token")


if __name__ == "__main__":
    unittest.main()
