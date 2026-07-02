from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.agents.account_profile import AccountProfileService
from backend.crypto import decrypt_credentials
from backend.database import Base


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
    last_auth_params = None

    @classmethod
    def from_client_config(cls, *args, **kwargs):
        cls.saw_relaxed_scope = False
        cls.last_auth_params = None
        return cls()

    def authorization_url(self, **kwargs):
        self.__class__.last_auth_params = kwargs
        return "https://accounts.google.com/o/oauth2/auth", "state"

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

    def test_build_auth_url_only_forces_consent_when_requested(self) -> None:
        fake_google_auth = types.ModuleType("google_auth_oauthlib")
        fake_flow_module = types.ModuleType("google_auth_oauthlib.flow")
        fake_flow_module.Flow = _FakeFlow

        with patch.dict(sys.modules, {
            "google_auth_oauthlib": fake_google_auth,
            "google_auth_oauthlib.flow": fake_flow_module,
            "googleapiclient": types.ModuleType("googleapiclient"),
        }):
            from backend.uploaders import youtube

            result = youtube.build_auth_url("7", force_consent=False)
            self.assertTrue(result["ok"])
            self.assertNotIn("prompt", _FakeFlow.last_auth_params)

            result = youtube.build_auth_url("7", force_consent=True)
            self.assertTrue(result["ok"])
            self.assertEqual(_FakeFlow.last_auth_params["prompt"], "consent")

    def test_youtube_credentials_preserve_existing_refresh_token(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)

        with Session(engine) as db:
            svc = AccountProfileService(db)
            acct = svc.create(platform="youtube", display_name="Canal")
            svc.set_credentials(acct.id, {
                "token": "old-access",
                "refresh_token": "long-refresh",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "scopes": ["youtube"],
            })

            svc.set_credentials(acct.id, {
                "token": "new-access",
                "refresh_token": None,
                "scopes": ["youtube"],
            })

            saved = decrypt_credentials(svc.get(acct.id).credentials_encrypted)
            self.assertEqual(saved["token"], "new-access")
            self.assertEqual(saved["refresh_token"], "long-refresh")
            self.assertEqual(saved["client_secret"], "client-secret")


if __name__ == "__main__":
    unittest.main()
