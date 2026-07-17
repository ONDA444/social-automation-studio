from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.agents import error_recovery


class SystemHealthSchedulerCheckTests(unittest.TestCase):
    """get_system_health()'s 'scheduler' check must distinguish a genuinely
    dead scheduler (red — every periodic recovery job has silently stopped)
    from a non-leader replica (green — it's never supposed to run one)."""

    def _run_with(self, sched_status):
        with patch("backend.scheduler.scheduler_status", return_value=sched_status), patch(
            "shutil.which", return_value="/usr/bin/ffmpeg"
        ):
            return error_recovery.get_system_health()

    def test_leader_and_alive_is_green(self) -> None:
        health = self._run_with({"leader": True, "running": True, "alive": True, "last_heartbeat_at": "x"})
        self.assertEqual(health["checks"]["scheduler"]["status"], "green")

    def test_leader_but_not_alive_is_red(self) -> None:
        health = self._run_with({"leader": True, "running": False, "alive": False, "last_heartbeat_at": None})
        self.assertEqual(health["checks"]["scheduler"]["status"], "red")
        self.assertEqual(health["status"], "red")

    def test_non_leader_replica_is_green_even_though_not_running(self) -> None:
        health = self._run_with({"leader": False, "running": False, "alive": False, "last_heartbeat_at": None})
        self.assertEqual(health["checks"]["scheduler"]["status"], "green")


if __name__ == "__main__":
    unittest.main()
