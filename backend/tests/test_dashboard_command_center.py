"""/dashboard overview — campos do Command Center (contas conectadas, falhas)."""
from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import JobStatus, PlatformAccount, VideoJob
from backend.routers.dashboard import overview


def _make_db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, future=True)()


class DashboardCommandCenterTests(unittest.TestCase):
    def test_accounts_connected_counts_only_active_with_credentials(self) -> None:
        db = _make_db()
        db.add_all([
            PlatformAccount(platform="youtube", display_name="A", credentials_encrypted="x"),
            PlatformAccount(platform="youtube", display_name="B", credentials_encrypted="y"),
            PlatformAccount(platform="tiktok", display_name="C"),  # sem credencial
            PlatformAccount(platform="instagram", display_name="D",
                            credentials_encrypted="z", status="paused"),
        ])
        db.commit()
        res = overview(db=db)
        self.assertEqual(res["accounts"], 4)
        self.assertEqual(res["accounts_connected"], {"youtube": 2})

    def test_recent_errors_newest_first_max_3(self) -> None:
        from datetime import datetime, timedelta

        db = _make_db()
        base = datetime.utcnow() - timedelta(minutes=10)
        for i in range(5):
            db.add(VideoJob(title=f"Erro {i}", status=JobStatus.ERROR,
                            error_message=f"boom {i}", current_agent="narrator",
                            updated_at=base + timedelta(minutes=i)))
        db.add(VideoJob(title="Ok", status=JobStatus.PUBLISHED))
        db.commit()
        res = overview(db=db)
        self.assertEqual(len(res["recent_errors"]), 3)
        self.assertEqual(res["recent_errors"][0]["title"], "Erro 4")
        self.assertNotIn("Ok", [e["title"] for e in res["recent_errors"]])
        self.assertEqual(res["recent_errors"][0]["current_agent"], "narrator")

    def test_empty_db_returns_empty_sections(self) -> None:
        db = _make_db()
        res = overview(db=db)
        self.assertEqual(res["accounts_connected"], {})
        self.assertEqual(res["recent_errors"], [])
