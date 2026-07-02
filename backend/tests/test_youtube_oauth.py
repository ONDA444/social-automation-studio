from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import patch


class _FakeCredentials:
    token = "access-token"
    refresh_token = "refresh-token"
    token_uri = "https://oauth2.googleapis.com/token"
    client_id = "client-id"
    client_secret = "client-secret"
    scopes = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]


class _FakeFlow:
    credentials = _FakeCredentials()
    redirect_uri = ""
    saw_relaxed_scope = False

    @classmethod
    def from_client_config(cls, *args, **kwargs):
        cls.saw_relaxed_scope = False
        return cls()

    def fetch_token(self, code: str) -> None:
        self.__class__.saw_relaxed_scope = os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE") == "1"


class YoutubeOauthTests(unittest.TestCase):
    def test_exchange_code_relaxes_extra_google_scopes_and_restores_env(self) -> None:
        fake_google_auth = types.ModuleType("google_auth_oauthlib")
        fake_flow_module = types.ModuleType("google_auth_oauthlib.flow")
        fake_flow_module.Flow = _FakeFlow

        previous = os.environ.pop("OAUTHLIB_RELAX_TOKEN_SCOPE", None)
        try:
            with patch.dict(sys.modules, {
                "google_auth_oauthlib": fake_google_auth,
                "google_auth_oauthlib.flow": fake_flow_module,
                "googleapiclient": types.ModuleType("googleapiclient"),
            }), patch("backend.uploaders.youtube._fetch_channel", return_value={"channel_id": "ch"}):
                from backend.uploaders import youtube

                result = youtube.exchange_code("code")

            self.assertTrue(result["ok"])
            self.assertTrue(_FakeFlow.saw_relaxed_scope)
            self.assertNotIn("OAUTHLIB_RELAX_TOKEN_SCOPE", os.environ)
            self.assertIn("https://www.googleapis.com/auth/drive.readonly", result["credentials"]["scopes"])
        finally:
            if previous is not None:
                os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = previous


if __name__ == "__main__":
    unittest.main()
