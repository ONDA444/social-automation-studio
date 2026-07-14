from __future__ import annotations

import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import PlatformAccount
from backend.routers.accounts import resume_account


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


class ResumeRequiresCredentialsTests(unittest.TestCase):
    def test_resume_rejects_account_without_credentials(self) -> None:
        """A disconnected/auth_error account (no credentials) must not be
        flippable back to 'active' via /resume — the scheduler would then try
        to publish on it and fail silently instead of prompting reconnection."""
        db = _make_session()
        acct = PlatformAccount(
            platform="youtube",
            display_name="Canal sem credenciais",
            status="disconnected",
            credentials_encrypted=None,
        )
        db.add(acct)
        db.commit()
        db.refresh(acct)

        with self.assertRaises(HTTPException) as ctx:
            resume_account(acct.id, db=db)
        self.assertEqual(ctx.exception.status_code, 400)

        db.refresh(acct)
        self.assertEqual(acct.status, "disconnected")

    def test_resume_allows_account_with_credentials(self) -> None:
        db = _make_session()
        acct = PlatformAccount(
            platform="youtube",
            display_name="Canal conectado",
            status="paused",
            credentials_encrypted="fake-encrypted-blob",
        )
        db.add(acct)
        db.commit()
        db.refresh(acct)

        result = resume_account(acct.id, db=db)
        self.assertEqual(result, {"ok": True})

        db.refresh(acct)
        self.assertEqual(acct.status, "active")


if __name__ == "__main__":
    unittest.main()
