"""Regression test: clone_voice used to swallow LMNT's actual rejection reason
behind r.raise_for_status()'s generic "400 Bad Request for url ..." text,
making every failure (bad audio format, too short, quota, whatever) look
identical and undebuggable. The real reason lives in the response body.

Also covers voice_library() -- lets an operator link an EXISTING LMNT voice
to a channel instead of re-recording/re-cloning one every time (see LMNT's
own "Custom Voices" dashboard, which accumulates a duplicate per clone
attempt)."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import PlatformAccount
from backend.routers.accounts import clone_voice, voice_library
from fastapi import HTTPException, UploadFile
from io import BytesIO


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


def _upload(size: int = 30_000) -> UploadFile:
    return UploadFile(filename="voice.webm", file=BytesIO(b"x" * size))


class CloneVoiceErrorSurfacingTests(unittest.TestCase):
    def _account(self, db):
        account = PlatformAccount(platform="youtube", display_name="Canal")
        db.add(account)
        db.flush()
        db.commit()
        return account

    def test_lmnt_rejection_reason_is_surfaced_not_the_generic_status_line(self) -> None:
        db = _make_session()
        account = self._account(db)

        fake_response = MagicMock()
        fake_response.status_code = 400
        fake_response.json.return_value = {"error": "audio duration too short (min 10s)"}
        fake_response.text = '{"error": "audio duration too short (min 10s)"}'

        fake_client = MagicMock()
        fake_client.post = AsyncMock(return_value=fake_response)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.config.settings.lmnt_api_key", "fake-key"), \
             patch("httpx.AsyncClient", return_value=fake_client):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(clone_voice(account.id, _upload(), db=db))

        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("audio duration too short", ctx.exception.detail)
        self.assertNotIn("Bad Request for url", ctx.exception.detail)

    def test_successful_clone_still_sets_the_account_voice(self) -> None:
        db = _make_session()
        account = self._account(db)

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {"id": "v_abc123"}

        fake_client = MagicMock()
        fake_client.post = AsyncMock(return_value=fake_response)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.config.settings.lmnt_api_key", "fake-key"), \
             patch("httpx.AsyncClient", return_value=fake_client):
            result = asyncio.run(clone_voice(account.id, _upload(), db=db))

        self.assertEqual(result["voice_id"], "v_abc123")
        db.refresh(account)
        self.assertEqual(account.preferred_voice, "v_abc123")


class VoiceLibraryTests(unittest.TestCase):
    def test_no_api_key_returns_a_clear_400(self) -> None:
        with patch("backend.config.settings.lmnt_api_key", ""):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(voice_library())
        self.assertEqual(ctx.exception.status_code, 400)

    def test_lists_voices_from_the_lmnt_response(self) -> None:
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = [
            {"id": "v_mateus", "name": "MATEUS", "state": "ready"},
            {"id": "v_anime_fut", "name": "voz-Anime fut", "state": "ready"},
        ]

        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_response)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.config.settings.lmnt_api_key", "fake-key"), \
             patch("httpx.AsyncClient", return_value=fake_client):
            result = asyncio.run(voice_library())

        self.assertEqual(len(result["voices"]), 2)
        self.assertEqual(result["voices"][0]["id"], "v_mateus")

    def test_lmnt_error_reason_is_surfaced_here_too(self) -> None:
        fake_response = MagicMock()
        fake_response.status_code = 401
        fake_response.json.return_value = {"error": "invalid api key"}
        fake_response.text = '{"error": "invalid api key"}'

        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_response)
        fake_client.__aenter__ = AsyncMock(return_value=fake_client)
        fake_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.config.settings.lmnt_api_key", "fake-key"), \
             patch("httpx.AsyncClient", return_value=fake_client):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(voice_library())

        self.assertIn("invalid api key", ctx.exception.detail)


class VoiceLibraryRouteOrderTests(unittest.TestCase):
    """Regression test for a real bug hit live in the browser: FastAPI/
    Starlette matches routes in declaration order, so
    GET /accounts/voice-library declared AFTER GET /accounts/{account_id}
    was unreachable -- "voice-library" got captured as account_id and failed
    int validation with a 422, every time."""

    def test_voice_library_is_declared_before_the_parameterized_account_route(self) -> None:
        from backend.routers.accounts import router

        paths_in_order = [
            (r.path, r.methods) for r in router.routes if hasattr(r, "path")
        ]
        voice_library_idx = next(
            i for i, (p, m) in enumerate(paths_in_order)
            if p == "/accounts/voice-library" and "GET" in m
        )
        account_id_idx = next(
            i for i, (p, m) in enumerate(paths_in_order)
            if p == "/accounts/{account_id}" and "GET" in m
        )
        self.assertLess(
            voice_library_idx, account_id_idx,
            "GET /accounts/voice-library must be declared before GET /accounts/{account_id} "
            "or Starlette routes 'voice-library' into the account_id path param.",
        )


if __name__ == "__main__":
    unittest.main()
