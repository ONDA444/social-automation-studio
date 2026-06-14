"""
Job dispatch with graceful degradation.

If Redis/Celery is reachable, enqueue as a Celery task (production / Railway).
Otherwise run the pipeline in-process on the API event loop (local dev without
Docker) — serialized by a semaphore so concurrent jobs don't thrash ffmpeg.
"""
from __future__ import annotations

import asyncio
import logging

from backend.config import settings

logger = logging.getLogger("studio.dispatch")

# Local-dev concurrency cap for the in-process path.
_MAX_INPROC = 1
_sem = asyncio.Semaphore(_MAX_INPROC)
_bg_tasks: set[asyncio.Task] = set()


def _redis_ok() -> bool:
    try:
        import redis

        r = redis.from_url(settings.redis_url, socket_connect_timeout=1)
        r.ping()
        return True
    except Exception:
        return False


def dispatch_job(job_id: int) -> str:
    """Enqueue production of a job. Returns the transport used."""
    if _redis_ok():
        try:
            from backend.pipeline.video_pipeline import process_job

            process_job.delay(job_id)
            return "celery"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Celery enqueue failed (%s); running in-process.", exc)
    _run_inprocess(run="pipeline", job_id=job_id)
    return "in_process"


def dispatch_publish(job_id: int) -> str:
    if _redis_ok():
        try:
            from backend.pipeline.video_pipeline import publish_job

            publish_job.delay(job_id)
            return "celery"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Celery publish enqueue failed (%s); running in-process.", exc)
    _run_inprocess(run="publish", job_id=job_id)
    return "in_process"


def _run_inprocess(run: str, job_id: int) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop (e.g. called from sync context) — run to completion in a thread.
        import threading

        threading.Thread(target=lambda: asyncio.run(_guarded(run, job_id)), daemon=True).start()
        return
    task = loop.create_task(_guarded(run, job_id))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def _guarded(run: str, job_id: int) -> None:
    async with _sem:
        if run == "pipeline":
            from backend.agents.orchestrator import run_pipeline

            await run_pipeline(job_id)
        else:
            from backend.agents.publisher import run_publish

            await run_publish(job_id)
