from __future__ import annotations

import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import PlatformAccount
from backend.models.video_job import JobStatus, VideoJob
from backend.routers.accounts import delete_account


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


class DeleteAccountGuardTests(unittest.TestCase):
    """Regression test: account_id is FK ondelete=SET NULL, so deleting an
    account with a job still PROCESSING/PUBLISHING would silently null it out
    on an in-flight job the orchestrator/publisher is still reading — the
    409 guard in delete_account (routers/accounts.py) must block that."""

    def test_delete_blocked_when_job_processing(self) -> None:
        db = _make_session()
        acct = PlatformAccount(platform="youtube", display_name="Canal com job ativo")
        db.add(acct)
        db.commit()
        db.refresh(acct)
        job = VideoJob(title="video em produção", account_id=acct.id, status=JobStatus.PROCESSING)
        db.add(job)
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            delete_account(acct.id, db=db)
        self.assertEqual(ctx.exception.status_code, 409)

        self.assertIsNotNone(db.get(PlatformAccount, acct.id))

    def test_delete_blocked_when_job_publishing(self) -> None:
        db = _make_session()
        acct = PlatformAccount(platform="youtube", display_name="Canal publicando")
        db.add(acct)
        db.commit()
        db.refresh(acct)
        job = VideoJob(title="video publicando", account_id=acct.id, status=JobStatus.PUBLISHING)
        db.add(job)
        db.commit()

        with self.assertRaises(HTTPException) as ctx:
            delete_account(acct.id, db=db)
        self.assertEqual(ctx.exception.status_code, 409)

        self.assertIsNotNone(db.get(PlatformAccount, acct.id))

    def test_delete_allowed_when_no_active_job(self) -> None:
        db = _make_session()
        acct = PlatformAccount(platform="youtube", display_name="Canal livre")
        db.add(acct)
        db.commit()
        db.refresh(acct)
        job = VideoJob(title="video publicado", account_id=acct.id, status=JobStatus.PUBLISHED)
        db.add(job)
        db.commit()

        result = delete_account(acct.id, db=db)
        self.assertEqual(result, {"deleted": acct.id})

        self.assertIsNone(db.get(PlatformAccount, acct.id))

    def test_delete_missing_account_returns_404(self) -> None:
        db = _make_session()
        with self.assertRaises(HTTPException) as ctx:
            delete_account(999, db=db)
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
