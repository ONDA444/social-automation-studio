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

# Job ids with a live in-process task RIGHT NOW (added when dispatched, removed
# when the task finishes). The runtime stuck-job sweep (scheduler.py) MUST
# check this before treating a job as orphaned: without it, a job that is
# merely SLOW (not dead) gets "recovered" and re-dispatched while its original
# task is still running, creating a second concurrent attempt for the same
# job. With only 2 publish slots, two such phantom duplicates alone exhaust
# the entire pool, permanently starving every other job — exactly the
# creeping deadlock this set exists to prevent.
_inflight: set[int] = set()
_inflight_lock = threading.Lock()


def is_inflight(job_id: int) -> bool:
    with _inflight_lock:
        return job_id in _inflight

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


def _is_manual_upload(job_id: int) -> bool:
    """True if this job was created from Agenda's "Enviar vídeo do PC" — its
    main_video_path is the user's own file and must never be regenerated."""
    from backend.database import SessionLocal
    from backend.models import VideoJob

    db = SessionLocal()
    try:
        job = db.get(VideoJob, job_id)
        return bool(job and job.mode == "from_manual_upload")
    finally:
        db.close()


def dispatch_job(job_id: int) -> str:
    """Enqueue production of a job. Returns the transport used.

    Celery is used ONLY when explicitly enabled (USE_CELERY=1) AND Redis is up —
    otherwise a deploy with Redis but no worker (e.g. single-service Railway)
    would push tasks to a queue nobody consumes, leaving jobs stuck in QUEUED.
    In-process is the safe default: it runs the pipeline on a dedicated worker
    loop inside this process.
    """
    # SAFE_BOOT halts heavy renders process-wide (boot recovery from an OOM crash
    # loop). The job stays QUEUED and resumes once the flag is cleared. Publishing
    # (light network I/O, no OOM risk) is intentionally left running.
    import os
    if os.getenv("SAFE_BOOT", "").strip().lower() in {"1", "true", "yes", "on"}:
        logger.warning("SAFE_BOOT ativo — render do job %s adiado (continua QUEUED).", job_id)
        return "deferred"
    # A manual PC upload must NEVER go through the full AI generation pipeline —
    # the user's original file is sacred, only SEO/metadata may be touched. Every
    # re-dispatch path (retry button, boot recovery, the stuck-job/error sweep)
    # calls this same dispatch_job(), so guarding here catches all of them at
    # once instead of patching each call site. Redirect to the one safe path.
    if _is_manual_upload(job_id):
        return dispatch_analyze_upload(job_id)
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
    # A manual PC upload's file only ever exists on the local disk of whichever
    # container received the original HTTP upload (backend/routers/schedule.py
    # saves it under settings.temp_dir, never to shared/durable storage). Every
    # caller of dispatch_publish() for this job (approve endpoint, republish
    # endpoint, _job_publish_due) is itself an HTTP/scheduler tick running
    # in-process on THAT SAME container — so publish must also run in-process
    # here, not hop to the separate Celery worker container, which never
    # received that file and would report it as "lost" even with zero restarts.
    if _is_manual_upload(job_id):
        _run_inprocess(run="publish", job_id=job_id)
        return "in_process"
    if settings.use_celery and _redis_ok():
        try:
            from backend.pipeline.video_pipeline import publish_job

            publish_job.delay(job_id)
            return "celery"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Celery publish enqueue failed (%s); running in-process.", exc)
    _run_inprocess(run="publish", job_id=job_id)
    return "in_process"


def dispatch_analyze_upload(job_id: int) -> str:
    """Analyze a manually-uploaded video (Agenda "Enviar video do PC") off the
    HTTP request thread. Always in-process (no Celery task defined for this —
    analysis is lightweight compared to a full render, and USE_CELERY is off
    by default anyway); shares the render semaphore since ffprobe/frame
    extraction is CPU-bound like a render, not network I/O like a publish."""
    _run_inprocess(run="analyze_upload", job_id=job_id)
    return "in_process"


def _run_inprocess(run: str, job_id: int) -> None:
    loop = _ensure_worker()
    with _inflight_lock:
        _inflight.add(job_id)
    fut = asyncio.run_coroutine_threadsafe(_guarded(run, job_id), loop)
    _pending.add(fut)

    def _done(f) -> None:
        _pending.discard(f)
        with _inflight_lock:
            _inflight.discard(job_id)
        try:
            f.result()
        except Exception:  # noqa: BLE001
            logger.exception("In-process %s of job %s failed", run, job_id)

    fut.add_done_callback(_done)


async def _guarded(run: str, job_id: int) -> None:
    # Render and publish use SEPARATE semaphores: a slow/stuck upload holds only
    # the publish pool and never blocks the render slot (and vice-versa).
    # analyze_upload shares the render semaphore (CPU-bound, like a render).
    if run in ("pipeline", "analyze_upload"):
        assert _render_sem is not None
        async with _render_sem:
            if run == "pipeline":
                from backend.agents.orchestrator import run_pipeline

                await run_pipeline(job_id)
            else:
                from backend.agents.manual_upload import run_analyze_upload

                await run_analyze_upload(job_id)
    else:
        assert _publish_sem is not None
        # TEMP DIAGNOSTIC: jobs stay in "publishing" with ZERO logs from
        # anywhere inside run_publish (not even our own CANARY_UPLOAD_LOOP
        # markers) even long after dispatch. Log semaphore-wait and entry so
        # the next log pull shows whether the task is stuck waiting for a
        # publish slot (semaphore exhausted) vs. stuck somewhere inside
        # run_publish before ever reaching the upload call.
        logger.warning("CANARY_GUARDED waiting for publish slot job=%s value=%s",
                        job_id, _publish_sem._value)
        async with _publish_sem:
            logger.warning("CANARY_GUARDED acquired publish slot job=%s", job_id)
            from backend.agents.publisher import run_publish

            await run_publish(job_id)
            logger.warning("CANARY_GUARDED run_publish returned job=%s", job_id)


def resume_account_blocked_jobs(account_id: int) -> list[int]:
    """A channel was just reconnected with a fresh, working OAuth token — auto-resume
    every job that was parked because its token had died.

    If the rendered video still exists on disk → re-publish it (cheap, NO LLM, no
    re-render). If the file is gone (the container recycled, ephemeral FS) → requeue
    a fresh render. Fire-and-forget: dispatch_* return immediately, so this never
    blocks the OAuth callback. Returns the resumed job ids.

    This is the complement to scheduler._job_retry_errored skipping auth failures:
    we don't retry while blocked (no wasted free-LLM quota), then resume the instant
    the blocker clears — exactly "the video goes back into production and publishes
    by itself" without the dead-token retry loop.
    """
    import os

    from sqlalchemy import or_, select

    from backend.database import SessionLocal
    from backend.models import JobStatus, VideoJob

    db = SessionLocal()
    resumed: list[int] = []
    try:
        rows = db.execute(
            select(VideoJob).where(
                VideoJob.account_id == account_id,
                VideoJob.status == JobStatus.ERROR,
                or_(
                    VideoJob.error_message.contains("invalid_grant"),
                    VideoJob.error_message.contains("expired or revoked"),
                    VideoJob.error_message.contains("credenciais conectadas"),
                ),
            )
        ).scalars().all()
        for job in rows:
            job.error_message = None
            job.approval_status = "approved"
            if job.main_video_path and os.path.exists(job.main_video_path):
                job.status = JobStatus.APPROVED        # render survives → publish only
                db.commit()
                dispatch_publish(job.id)
            else:
                job.status = JobStatus.QUEUED          # file gone → must re-render
                job.progress = 0
                job.current_agent = None
                db.commit()
                dispatch_job(job.id)
            resumed.append(job.id)
        if resumed:
            logger.info("Reconexão da conta %s — %d job(s) retomado(s) automaticamente: %s",
                        account_id, len(resumed), resumed)
        return resumed
    except Exception:  # noqa: BLE001 — must never break the OAuth callback
        db.rollback()
        logger.exception("resume_account_blocked_jobs falhou (conta %s)", account_id)
        return resumed
    finally:
        db.close()
