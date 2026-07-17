from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from backend import scheduler


class SchedulerStatusTests(unittest.TestCase):
    """scheduler_status()/ensure_scheduler_running() are what the health check
    and the 'Corrigir sistema' button rely on to notice — and recover from — a
    scheduler thread that's alive as an object but has stopped actually
    ticking (the one failure mode nothing else in the system could see)."""

    def setUp(self) -> None:
        self._orig_scheduler = scheduler._scheduler
        self._orig_heartbeat_at = scheduler._last_heartbeat_at
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        scheduler._scheduler = self._orig_scheduler
        scheduler._last_heartbeat_at = self._orig_heartbeat_at

    def test_never_started_is_not_alive(self) -> None:
        scheduler._scheduler = None
        scheduler._last_heartbeat_at = None
        status = scheduler.scheduler_status()
        self.assertFalse(status["running"])
        self.assertFalse(status["alive"])

    def test_running_with_recent_heartbeat_is_alive(self) -> None:
        scheduler._scheduler = MagicMock(running=True)
        scheduler._last_heartbeat_at = datetime.utcnow()
        status = scheduler.scheduler_status()
        self.assertTrue(status["running"])
        self.assertTrue(status["alive"])

    def test_running_but_stale_heartbeat_is_not_alive(self) -> None:
        """The thread object exists and .running is True, but no heartbeat
        landed in the last 90s — a deadlocked/frozen scheduler thread."""
        scheduler._scheduler = MagicMock(running=True)
        scheduler._last_heartbeat_at = datetime.utcnow() - timedelta(minutes=5)
        status = scheduler.scheduler_status()
        self.assertTrue(status["running"])
        self.assertFalse(status["alive"])

    def test_ensure_scheduler_running_is_a_noop_when_already_running(self) -> None:
        scheduler._scheduler = MagicMock(running=True)
        with patch("backend.scheduler.start_scheduler") as mock_start:
            result = scheduler.ensure_scheduler_running()
        self.assertTrue(result)
        mock_start.assert_not_called()

    def test_ensure_scheduler_running_restarts_a_dead_scheduler(self) -> None:
        scheduler._scheduler = MagicMock(running=False)

        def fake_start():
            scheduler._scheduler = MagicMock(running=True)

        with patch("backend.scheduler.start_scheduler", side_effect=fake_start) as mock_start:
            result = scheduler.ensure_scheduler_running()
        self.assertTrue(result)
        mock_start.assert_called_once()

    def test_ensure_scheduler_running_on_non_leader_stays_down(self) -> None:
        """A non-leader replica must never spin up its own scheduler — matches
        start_scheduler's own ownership guard (see SCHEDULER_LEADER)."""
        scheduler._scheduler = None

        def fake_start():
            pass  # non-leader: start_scheduler() itself no-ops

        with patch("backend.scheduler.start_scheduler", side_effect=fake_start):
            result = scheduler.ensure_scheduler_running()
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
