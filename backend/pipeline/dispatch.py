"""
Job dispatch with graceful degradation.

If Redis/Celery is reachable, enqueue as a Celery task (production / Railway).
Otherwise run the pipeline in-process on a SINGLE dedicated background worker
loop (local dev without Docker) — serialized so concurrent jobs don't thrash
ffmpeg.

Why a dedicated loop: the previous implementation spawned `asyncio.run()` in an
ad-hoc thread per job, which creates a brand-new event loop each time while the
module-level `asyncio.Semaphore` stays bound to the FIRST loop. Every job after
the first then raised `RuntimeError: Semaphore is bound to a different event
loop` and got stuck in `queued` forever. Running everything on one long-lived
loop keeps the semaphore valid and truly serializes the work.
"""
from __future__ import annotations

import asyncio
import logging
import threading

from backend.config import settings

logger = logging.getLogger("studio.dispatch")

# Local-dev concurrency cap for the in-process path. Rendering is CPU/RAM heavy
# (ffmpeg) and MUST stay serial (1 at a time). Publishing is network I/O — give it
# its OWN small pool so a slow or stuck upload can NEVER starve the render slot
# (a missing-file publish used to hold the single shared slot for minutes, leaving
# freshly-created jobs stuck in QUEUED with "nothing generating").
_MAX_RENDER = 1
_MAX_PUBLISH = 2

_worker_loop: asyncio.AbstractEventLoop | None = None
_worker_lock = threading.Lock()
_render_sem: asyncio.Semaphore | None = None
_publish_sem: asyncio.Semaphore | None = None
_pending: set = set()

# Cache the Redis probe briefly: each probe costs ~0.4s when Redis is down, and
# a batch can dispatch hundreds of jobs back-to-back.
_redis_cache: dict = {"ok": None, "at": 0.0}
_REDIS_TTL = 5.0


def _ensure_worker() -> asyncio.AbstractEventLoop:
    """Lazily start the dedicated worker loop on a daemon thread."""
    global _worker_loop, _render_sem, _publish_sem
    if _worker_loop is not None:
        return _worker_loop
    with _worker_lock:
        if _worker_loop is not None:
            return _worker_loop
        loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        threading.Thread(target=_run, daemon=True, name="studio-pipeline-worker").start()

        # Create the Semaphores *inside* this loop so `async with` binds to it.
        async def _mk() -> tuple[asyncio.Semaphore, asyncio.Semaphore]:
            return asyncio.Semaphore(_MAX_RENDER), asyncio.Semaphore(_MAX_PUBLISH)

        _render_sem, _publish_sem = asyncio.run_coroutine_threadsafe(_mk(), loop).result(timeout=5)
        _worker_loop = loop
        logger.info("In-process pipeline worker started (render cap=%d, publish cap=%d).",
                    _MAX_RENDER, _MAX_PUBLISH)
    return _worker_loop


def _redis_ok() -> bool:
    import time

    now = time.monotonic()
    if _redis_cache["ok"] is not None and (now - _redis_cache["at"]) < _REDIS_TTL:
        return _redis_cache["ok"]
    ok = False
    try:
        import redis

        r = redis.from_url(settings.redis_url, socket_connect_timeout=1)
        r.ping()
        ok = True
    except Exception:
        ok = False
    _redis_cache["ok"] = ok
    _redis_cache["at"] = now
    return ok


def dispatch_job(job_id: int) -> str:
    """Enqueue production of a job. Returns the transport used.

    Celery is used ONLY when explicitly enabled (USE_CELERY=1) AND Redis is up —
    otherwise a deploy with Redis but no worker (e.g. single-service Railway)
    would push tasks to a queue nobody consumes, leaving jobs stuck in QUEUED.
    In-process is the safe default: it runs the pipeline on a dedicated worker
    loop inside this process.
    """
    if settings.use_celery and _redis_ok():
        try:
            from backend.pipeline.video_pipeline import process_job

            process_job.delay(job_id)
            return "celery"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Celery enqueue failed (%s); running in-process.", exc)
    _run_inprocess(run="pipeline", job_id=job_id)
    return "in_process"


def dispatch_publish(job_id: int) -> str:
    if settings.use_celery and _redis_ok():
        try:
            from backend.pipeline.video_pipeline import publish_job

            publish_job.delay(job_id)
            return "celery"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Celery publish enqueue failed (%s); running in-process.", exc)
    _run_inprocess(run="publish", job_id=job_id)
    return "in_process"


def _run_inprocess(run: str, job_id: int) -> None:
    loop = _ensure_worker()
    fut = asyncio.run_coroutine_threadsafe(_guarded(run, job_id), loop)
    _pending.add(fut)

    def _done(f) -> None:
        _pending.discard(f)
        try:
            f.result()
        except Exception:  # noqa: BLE001
            logger.exception("In-process %s of job %s failed", run, job_id)

    fut.add_done_callback(_done)


async def _guarded(run: str, job_id: int) -> None:
    # Render and publish use SEPARATE semaphores: a slow/stuck upload holds only
    # the publish pool and never blocks the render slot (and vice-versa).
    if run == "pipeline":
        assert _render_sem is not None
        async with _render_sem:
            from backend.agents.orchestrator import run_pipeline

            await run_pipeline(job_id)
    else:
        assert _publish_sem is not None
        async with _publish_sem:
            from backend.agents.publisher import run_publish

            await run_publish(job_id)
