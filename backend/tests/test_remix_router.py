"""Regression tests for the /remix router.

Covers the two gaps found in audit: create_remix built a VideoJob straight
from user input with no check that account_id/target_platforms were real,
and there was no test pinning down the content_type leak fix (a reference's
inferred content_type must never override the user's chosen theme type).
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import PlatformAccount
from backend.routers.remix import AnalyzeRequest, RemixCreate, analyze, create_remix


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


def _account(db):
    account = PlatformAccount(platform="youtube", display_name="Canal", niche="geral")
    db.add(account)
    db.flush()
    return account


class CreateRemixValidationTests(unittest.TestCase):
    def test_missing_account_id_400s_before_dispatch(self) -> None:
        db = _make_session()
        payload = RemixCreate(title="T", style_dna={"content_type": "sports_highlights"},
                               account_id=999)

        with patch("backend.routers.remix.dispatch_job") as fake_dispatch, \
             self.assertRaises(HTTPException) as ctx:
            asyncio.run(create_remix(payload, db=db))

        self.assertEqual(ctx.exception.status_code, 400)
        fake_dispatch.assert_not_called()

    def test_invalid_target_platform_400s_before_dispatch(self) -> None:
        db = _make_session()
        account = _account(db)
        payload = RemixCreate(title="T", style_dna={"content_type": "sports_highlights"},
                               account_id=account.id, target_platforms=["youtube", "myspace"])

        with patch("backend.routers.remix.dispatch_job") as fake_dispatch, \
             self.assertRaises(HTTPException) as ctx:
            asyncio.run(create_remix(payload, db=db))

        self.assertEqual(ctx.exception.status_code, 400)
        fake_dispatch.assert_not_called()

    def test_valid_account_and_platforms_are_accepted(self) -> None:
        db = _make_session()
        account = _account(db)
        payload = RemixCreate(title="T", style_dna={"content_type": "sports_highlights"},
                               account_id=account.id, target_platforms=["youtube", "tiktok"])

        with patch("backend.routers.remix.dispatch_job", return_value="in_process") as fake_dispatch:
            result = asyncio.run(create_remix(payload, db=db))

        fake_dispatch.assert_called_once()
        self.assertEqual(result["job"]["account_id"], account.id)

    def test_no_account_id_skips_the_account_check(self) -> None:
        db = _make_session()
        payload = RemixCreate(title="T", style_dna={"content_type": "sports_highlights"})

        with patch("backend.routers.remix.dispatch_job", return_value="in_process") as fake_dispatch:
            result = asyncio.run(create_remix(payload, db=db))

        fake_dispatch.assert_called_once()
        self.assertIsNone(result["job"]["account_id"])


class ContentTypeLeakRegressionTests(unittest.TestCase):
    """A reference's inferred content_type (StyleDNA) must never override the
    user's chosen theme type — a football reference used to turn "Animated
    Heroes" into a soccer video. StyleDNA drives STYLE only."""

    def test_reference_content_type_never_overrides_the_users_theme(self) -> None:
        db = _make_session()
        payload = RemixCreate(
            title="Animated Heroes",
            style_dna={"content_type": "sports_highlights"},  # football reference
            content_type=None,  # user did not choose a type
        )

        with patch("backend.routers.remix.dispatch_job", return_value="in_process"):
            result = asyncio.run(create_remix(payload, db=db))

        self.assertEqual(result["job"]["content_type"], "film_recap_ai_images")

    def test_users_explicit_content_type_is_honoured_over_the_reference(self) -> None:
        db = _make_session()
        payload = RemixCreate(
            title="Animated Heroes",
            style_dna={"content_type": "sports_highlights"},
            content_type="quote_viral",
        )

        with patch("backend.routers.remix.dispatch_job", return_value="in_process"):
            result = asyncio.run(create_remix(payload, db=db))

        self.assertEqual(result["job"]["content_type"], "quote_viral")


class CreateRemixSourceTests(unittest.TestCase):
    def test_missing_source_and_style_dna_400s(self) -> None:
        db = _make_session()
        payload = RemixCreate(title="T")

        with patch("backend.routers.remix.dispatch_job") as fake_dispatch, \
             self.assertRaises(HTTPException) as ctx:
            asyncio.run(create_remix(payload, db=db))

        self.assertEqual(ctx.exception.status_code, 400)
        fake_dispatch.assert_not_called()

    def test_source_without_style_dna_runs_the_analyzer(self) -> None:
        db = _make_session()
        payload = RemixCreate(title="T", source="https://example.com/ref.mp4")
        fake_dna = {"content_type": "film_recap_ai_images"}

        with patch("backend.routers.remix.AnalyzerAgent.execute",
                   new_callable=AsyncMock, return_value=fake_dna) as fake_execute, \
             patch("backend.routers.remix.dispatch_job", return_value="in_process"):
            result = asyncio.run(create_remix(payload, db=db))

        fake_execute.assert_called_once_with(source="https://example.com/ref.mp4")
        self.assertEqual(result["job"]["reference_url"], "https://example.com/ref.mp4")


class AnalyzeEndpointTests(unittest.TestCase):
    def test_analyzer_failure_becomes_a_400(self) -> None:
        payload = AnalyzeRequest(source="https://example.com/ref.mp4")

        with patch("backend.routers.remix.AnalyzerAgent.execute",
                   new_callable=AsyncMock, side_effect=RuntimeError("boom")), \
             self.assertRaises(HTTPException) as ctx:
            asyncio.run(analyze(payload))

        self.assertEqual(ctx.exception.status_code, 400)

    def test_analyzer_success_returns_the_style_dna(self) -> None:
        payload = AnalyzeRequest(source="https://example.com/ref.mp4")
        fake_dna = {"content_type": "sports_highlights"}

        with patch("backend.routers.remix.AnalyzerAgent.execute",
                   new_callable=AsyncMock, return_value=fake_dna):
            result = asyncio.run(analyze(payload))

        self.assertEqual(result, {"style_dna": fake_dna})


if __name__ == "__main__":
    unittest.main()
