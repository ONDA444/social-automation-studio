from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import PlatformAccount
from backend.routers import system as system_router


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


class FixAllTests(unittest.TestCase):
    """'/system/fix-all' is the Config page's single 'Corrigir sistema' button:
    it must run every self-heal step (video jobs, per-account reconnect
    resurrection, Drive resurrection, stuck-job sweep, scheduler restart) and
    keep going even if one step blows up, then report anything left over that
    genuinely needs a human."""

    def test_runs_every_step_and_aggregates_results(self) -> None:
        db = _make_session()
        acct1 = PlatformAccount(platform="youtube", display_name="Canal 1", niche="geral", status="active")
        acct2 = PlatformAccount(platform="tiktok", display_name="Canal 2", niche="geral", status="active")
        paused = PlatformAccount(platform="youtube", display_name="Canal Pausado", niche="geral", status="auth_error")
        db.add_all([acct1, acct2, paused])
        db.commit()
        acct1_id, acct2_id = acct1.id, acct2.id

        fake_video_jobs_result = {"fixed": [10, 11], "fixed_count": 2, "skipped": [], "skipped_count": 0}

        def fake_resume_account(account_id):
            return [101] if account_id == acct1_id else []

        with patch("backend.routers.system.fix_errors", return_value=fake_video_jobs_result), patch(
            "backend.pipeline.dispatch.resume_account_blocked_jobs", side_effect=fake_resume_account
        ) as mock_resume_acct, patch(
            "backend.pipeline.dispatch.resume_drive_blocked_jobs", return_value=[202]
        ) as mock_resume_drive, patch(
            "backend.scheduler._job_recover_stuck_publishing"
        ) as mock_sweep, patch(
            "backend.scheduler.scheduler_status", return_value={"leader": True, "running": False, "alive": False, "last_heartbeat_at": None}
        ), patch(
            "backend.scheduler.ensure_scheduler_running", return_value=True
        ) as mock_ensure, patch(
            "backend.agents.error_recovery.get_system_health",
            return_value={"status": "yellow", "checks": {
                "database": {"status": "green", "detail": "ok"},
                "redis": {"status": "yellow", "detail": "indisponível (modo in-process)"},
            }},
        ):
            result = system_router.fix_all(db=db)

        self.assertEqual(result["video_jobs"], fake_video_jobs_result)
        # Only the two ACTIVE accounts are attempted — the auth_error one is skipped.
        self.assertEqual(mock_resume_acct.call_count, 2)
        self.assertEqual(result["accounts_resumed"], {acct1_id: [101]})
        mock_resume_drive.assert_called_once()
        self.assertEqual(result["drive_resumed"], [202])
        mock_sweep.assert_called_once()
        self.assertTrue(result["stuck_jobs_swept"])
        mock_ensure.assert_called_once()
        self.assertTrue(result["scheduler"]["was_down"])
        self.assertTrue(result["scheduler"]["now_running"])
        self.assertEqual(result["needs_human"], [{"check": "redis", "status": "yellow", "detail": "indisponível (modo in-process)"}])

    def test_one_step_blowing_up_does_not_block_the_rest(self) -> None:
        db = _make_session()
        with patch("backend.routers.system.fix_errors", side_effect=RuntimeError("boom")), patch(
            "backend.pipeline.dispatch.resume_drive_blocked_jobs", return_value=[]
        ) as mock_resume_drive, patch(
            "backend.scheduler._job_recover_stuck_publishing"
        ) as mock_sweep, patch(
            "backend.scheduler.scheduler_status", return_value={"leader": True, "running": True, "alive": True, "last_heartbeat_at": "x"}
        ), patch(
            "backend.scheduler.ensure_scheduler_running", return_value=True
        ), patch(
            "backend.agents.error_recovery.get_system_health",
            return_value={"status": "green", "checks": {"database": {"status": "green", "detail": "ok"}}},
        ):
            result = system_router.fix_all(db=db)

        # fix_errors blew up, but every later step must still have run.
        self.assertEqual(result["video_jobs"], {"fixed": [], "fixed_count": 0, "skipped": [], "skipped_count": 0})
        mock_resume_drive.assert_called_once()
        mock_sweep.assert_called_once()
        self.assertFalse(result["scheduler"]["was_down"])
        self.assertEqual(result["needs_human"], [])


if __name__ == "__main__":
    unittest.main()
