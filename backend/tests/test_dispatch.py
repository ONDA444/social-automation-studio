from __future__ import annotations

import asyncio
import threading
import time
import unittest
from unittest.mock import patch

from backend.config import settings
from backend.pipeline import dispatch


class InflightGuardTests(unittest.TestCase):
    """dispatch.is_inflight()/_run_inprocess() exist specifically to stop the
    same job from being processed twice concurrently (see the comment above
    `_inflight` in backend/pipeline/dispatch.py and publisher.py's historic
    duplicate-upload bug). This was previously untested."""

    def setUp(self) -> None:
        # Reset module-level singleton state so tests don't leak into each other.
        dispatch._worker_loop = None
        dispatch._render_sem = None
        dispatch._publish_sem = None
        dispatch._pending.clear()
        with dispatch._inflight_lock:
            dispatch._inflight.clear()

    def test_run_inprocess_marks_job_inflight_until_task_finishes(self) -> None:
        release = threading.Event()
        started = threading.Event()

        async def fake_run_pipeline(job_id: int) -> None:
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.01)

        with patch.object(settings, "use_celery", False), patch(
            "backend.agents.orchestrator.run_pipeline", fake_run_pipeline
        ):
            dispatch._run_inprocess(run="pipeline", job_id=999)

            self.assertTrue(started.wait(timeout=5), "task never started")
            # While the task is actively running, is_inflight() must report True
            # so a re-dispatch attempt (e.g. from the stuck-job sweep) is skipped.
            self.assertTrue(dispatch.is_inflight(999))

            release.set()

            deadline = time.monotonic() + 5
            while dispatch.is_inflight(999) and time.monotonic() < deadline:
                time.sleep(0.02)

            # Once the task completes, the guard must clear so future work on
            # this job_id is not blocked forever.
            self.assertFalse(dispatch.is_inflight(999))

    def test_is_inflight_false_for_job_with_no_active_task(self) -> None:
        with patch.object(settings, "use_celery", False):
            self.assertFalse(dispatch.is_inflight(123456))


if __name__ == "__main__":
    unittest.main()
