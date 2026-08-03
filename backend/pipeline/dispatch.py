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
from datetime import datetime, timedelta

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


_CELERY_TASK_NAMES = ("pipeline.process_job", "pipeline.publish_job")


def _celery_task_job_ids(tasks) -> set:
    ids: set = set()
    for task in tasks:
        if task.get("name") not in _CELERY_TASK_NAMES:
            continue
        args = list(task.get("args") or []) + list((task.get("kwargs") or {}).values())
        # publish_job's kwargs include `platforms`, a list — not hashable, so
        # filter to ints (job ids) before building a set instead of blindly
        # updating with every raw arg/kwarg value.
        ids.update(a for a in args if isinstance(a, int))
    return ids


def celery_inflight_ids() -> set:
    """One broker round-trip covering every state a Celery task can be in
    BEFORE it shows up as done: 'active' (a worker is executing it right
    now), 'reserved' (already pulled into a worker's local prefetch buffer,
    not started yet) and 'scheduled' (an ETA/countdown task waiting to fire).

    'active' alone is blind to a task that is genuinely still waiting its
    turn — plausible with a single busy worker, or the in-process render cap
    of 1 — so a stuck-job sweep relying on it only would "recover" and
    re-dispatch a second task for a job whose original attempt hasn't even
    started yet.

    Callers with MULTIPLE candidates in one sweep (scheduler.py's
    _job_recover_stuck_publishing/_job_recover_stuck_queued) should call this
    ONCE per tick and pass the result to is_inflight() for every candidate —
    each inspect() call is a blocking broker RPC (up to ~1s per state), and
    the result doesn't change mid-sweep.
    """
    if not settings.use_celery:
        return set()
    ids: set = set()
    try:
        from backend.pipeline.celery_app import app as celery_app

        insp = celery_app.control.inspect(timeout=1.0)
        for tasks in (insp.active() or {}).values():
            ids |= _celery_task_job_ids(tasks)
        for tasks in (insp.reserved() or {}).values():
            ids |= _celery_task_job_ids(tasks)
        # scheduled() wraps each task under a "request" key instead of
        # returning the task dict directly like active()/reserved() do.
        for entries in (insp.scheduled() or {}).values():
            ids |= _celery_task_job_ids(entry.get("request", entry) for entry in entries)
    except Exception as exc:  # noqa: BLE001 — never let a broker hiccup fail this check
        logger.warning("celery_inflight_ids: Celery inspect failed (%s)", exc)
    return ids


def is_inflight(job_id: int, celery_ids: set | None = None) -> bool:
    """True if `job_id` has a live task right now — either on this process's
    own in-process worker, or (USE_CELERY=1) on a Celery worker.

    `celery_ids`, if given, is a pre-fetched celery_inflight_ids() snapshot —
    pass it when checking many job ids in one sweep so this does zero extra
    broker RPCs per call. Omit it for a one-off check; a fresh snapshot is
    fetched on demand.
    """
    with _inflight_lock:
        if job_id in _inflight:
            return True
    # The in-process set above only ever gets populated by _run_inprocess, so it
    # is blind to work actually executing on a SEPARATE Celery worker container
    # (USE_CELERY=1). Without this check, the web container's stuck-job sweep
    # (scheduler._job_recover_stuck_publishing) would see nothing "inflight" for
    # a job that is genuinely still uploading in the worker, mark it orphaned,
    # and re-dispatch a second concurrent publish for the same job — risking a
    # duplicate upload to the same platform. Ask Celery's own workers whether
    # any of them currently has a task for this job_id.
    if settings.use_celery:
        ids = celery_ids if celery_ids is not None else celery_inflight_ids()
        return job_id in ids
    return False

# Cache the Redis probe briefly: each probe costs up to _REDIS_PING_TIMEOUT
# when Redis is down, and a batch can dispatch hundreds of jobs back-to-back.
# dispatch_job()/dispatch_publish() call this SYNCHRONOUSLY from HTTP request
# threads too (approve/republish endpoints), not just the scheduler — during a
# real Redis outage, back off harder (_REDIS_DOWN_TTL) so the blocking probe
# only lands once every 30s instead of every 5s for the whole incident.
_redis_cache: dict = {"ok": None, "at": 0.0}
_REDIS_TTL = 5.0
_REDIS_DOWN_TTL = 30.0
# A healthy Redis answers in low single-digit milliseconds; this only ever
# gets fully paid when Redis is actually unreachable, so keeping it small
# caps how long an HTTP request thread can block on a dead broker.
_REDIS_PING_TIMEOUT = 0.3

# Heartbeat for the worker loop — the analogue of scheduler.py's
# _last_heartbeat_at/_HEARTBEAT_STALE_AFTER, which exists specifically
# because "a frozen/deadlocked scheduler thread would otherwise be
# invisible" (see scheduler.py). The same is true here: if this thread dies
# or deadlocks, dispatch_job()/dispatch_publish() keep returning normally
# ("in_process", no exception — _run_inprocess never awaits anything) while
# asyncio.run_coroutine_threadsafe silently queues callbacks on a loop nobody
# is pumping anymore. Nothing dispatched from that point on ever runs, with
# no error anywhere. worker_status() below is what makes that state visible.
_worker_last_heartbeat_at: datetime | None = None
_WORKER_HEARTBEAT_INTERVAL = 10.0
_WORKER_HEARTBEAT_STALE_AFTER = timedelta(seconds=30)


def _ensure_worker() -> asyncio.AbstractEventLoop:
    """Lazily start the dedicated worker loop on a daemon thread."""
    global _worker_loop, _render_sem, _publish_sem, _worker_last_heartbeat_at
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
        _worker_last_heartbeat_at = datetime.utcnow()

        # Self-rescheduling heartbeat: proof the loop is still actually being
        # pumped, not just that the thread object exists. Fire-and-forget —
        # this coroutine runs forever on `loop`, so we never await its result.
        async def _heartbeat() -> None:
            global _worker_last_heartbeat_at
            while True:
                await asyncio.sleep(_WORKER_HEARTBEAT_INTERVAL)
                _worker_last_heartbeat_at = datetime.utcnow()

        asyncio.run_coroutine_threadsafe(_heartbeat(), loop)
        logger.info("In-process pipeline worker started (render cap=%d, publish cap=%d).",
                    _MAX_RENDER, _MAX_PUBLISH)
    return _worker_loop


def worker_status() -> dict:
    """Liveness snapshot for the in-process worker loop — see the
    _worker_last_heartbeat_at comment above for why this exists. Used by
    get_system_health() so a frozen worker shows up the same way a frozen
    scheduler does (backend.scheduler.scheduler_status)."""
    started = _worker_loop is not None
    stale = (
        _worker_last_heartbeat_at is None
        or (datetime.utcnow() - _worker_last_heartbeat_at) > _WORKER_HEARTBEAT_STALE_AFTER
    )
    return {
        "started": started,
        "last_heartbeat_at": _worker_last_heartbeat_at.isoformat() if _worker_last_heartbeat_at else None,
        "alive": (not started) or (not stale),
        "pending_tasks": len(_pending),
        "inflight_jobs": len(_inflight),
    }


def _redis_ok() -> bool:
    import time

    now = time.monotonic()
    ttl = _REDIS_TTL if _redis_cache["ok"] else _REDIS_DOWN_TTL
    if _redis_cache["ok"] is not None and (now - _redis_cache["at"]) < ttl:
        return _redis_cache["ok"]
    ok = False
    try:
        import redis

        r = redis.from_url(settings.redis_url, socket_connect_timeout=_REDIS_PING_TIMEOUT)
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


def _job_mode(job_id: int) -> str | None:
    """Single round-trip read of VideoJob.mode.

    dispatch_job() used to open TWO separate SessionLocal() sessions back to
    back on EVERY dispatch in the system — _is_manual_upload, then, if that
    came back False, a second full from_ready_video query
    (scheduler._try_create_ready_video_job's counterpart) — just to read the
    same column off the same row. This is the one query dispatch_job
    actually needs; both checks below now read from it."""
    from backend.database import SessionLocal
    from backend.models import VideoJob

    db = SessionLocal()
    try:
        job = db.get(VideoJob, job_id)
        return job.mode if job else None
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
    #
    # A Drive "ready video" job must NEVER go through the full AI generation
    # pipeline either — same reasoning as manual uploads above, and the same
    # every-re-dispatch-path guarantee (this is the one chokepoint every retry
    # path calls). Without this, a Drive job that errored (e.g. a transient
    # download failure) and got retried — by the user's Retry button or the
    # automatic 72h error-resurrection sweep — would silently get a brand-new
    # AI-scripted video (TTS + generated images) instead of retrying the
    # Drive download, defeating "Drive only" mode entirely.
    mode = _job_mode(job_id)
    if mode == "from_manual_upload":
        return dispatch_analyze_upload(job_id)
    if mode == "from_ready_video":
        return dispatch_retry_ready_video(job_id)
    if settings.use_celery and _redis_ok():
        try:
            from backend.pipeline.video_pipeline import process_job

            process_job.delay(job_id)
            return "celery"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Celery enqueue failed (%s); running in-process.", exc)
    _run_inprocess(run="pipeline", job_id=job_id)
    return "in_process"


def dispatch_retry_ready_video(job_id: int) -> str:
    """Re-download and re-package a `from_ready_video` job that errored out —
    always in-process (no Celery task defined; this is a light, occasional
    recovery path, not a hot one). Shares the render semaphore like
    dispatch_analyze_upload since it's CPU/network-bound, not upload I/O."""
    _run_inprocess(run="retry_ready_video", job_id=job_id)
    return "in_process"


def dispatch_publish(job_id: int, platforms: list | None = None) -> str:
    # `platforms`, if given, restricts this dispatch to a SUBSET of the job's
    # target_platforms — used by the orchestrator's schedule-mode early
    # dispatch to push only YouTube ahead of time (the only uploader with a
    # native publishAt), leaving TikTok/Instagram for the next
    # _job_publish_due tick at the actual scheduled_at.
    # A manual PC upload's file only ever exists on the local disk of whichever
    # container received the original HTTP upload (backend/routers/schedule.py
    # saves it under settings.temp_dir, never to shared/durable storage). Every
    # caller of dispatch_publish() for this job (approve endpoint, republish
    # endpoint, _job_publish_due) is itself an HTTP/scheduler tick running
    # in-process on THAT SAME container — so publish must also run in-process
    # here, not hop to the separate Celery worker container, which never
    # received that file and would report it as "lost" even with zero restarts.
    if _is_manual_upload(job_id):
        _run_inprocess(run="publish", job_id=job_id, platforms=platforms)
        return "in_process"
    if settings.use_celery and _redis_ok():
        try:
            from backend.pipeline.video_pipeline import publish_job

            publish_job.delay(job_id, platforms=platforms)
            return "celery"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Celery publish enqueue failed (%s); running in-process.", exc)
    _run_inprocess(run="publish", job_id=job_id, platforms=platforms)
    return "in_process"


def dispatch_analyze_upload(job_id: int) -> str:
    """Analyze a manually-uploaded video (Agenda "Enviar video do PC") off the
    HTTP request thread. Always in-process (no Celery task defined for this —
    analysis is lightweight compared to a full render, and USE_CELERY is off
    by default anyway); shares the render semaphore since ffprobe/frame
    extraction is CPU-bound like a render, not network I/O like a publish."""
    _run_inprocess(run="analyze_upload", job_id=job_id)
    return "in_process"


def _run_inprocess(run: str, job_id: int, platforms: list | None = None) -> None:
    loop = _ensure_worker()
    with _inflight_lock:
        _inflight.add(job_id)
    fut = asyncio.run_coroutine_threadsafe(_guarded(run, job_id, platforms=platforms), loop)
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


async def _guarded(run: str, job_id: int, platforms: list | None = None) -> None:
    # Render and publish use SEPARATE semaphores: a slow/stuck upload holds only
    # the publish pool and never blocks the render slot (and vice-versa).
    # analyze_upload/retry_ready_video share the render semaphore (CPU/network
    # -bound like a render, not upload I/O like a publish).
    if run in ("pipeline", "analyze_upload", "retry_ready_video"):
        assert _render_sem is not None
        async with _render_sem:
            if run == "pipeline":
                from backend.agents.orchestrator import run_pipeline

                await run_pipeline(job_id)
            elif run == "analyze_upload":
                from backend.agents.manual_upload import run_analyze_upload

                await run_analyze_upload(job_id)
            else:
                from backend.scheduler import retry_ready_video_job

                await asyncio.to_thread(retry_ready_video_job, job_id)
    else:
        assert _publish_sem is not None
        async with _publish_sem:
            from backend.agents.publisher import run_publish

            await run_publish(job_id, platforms=platforms)


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

    ATOMIC per-job claim (UPDATE ... WHERE status='error' + rowcount), same
    technique every periodic job in scheduler.py uses: this function is
    called from HTTP triggers with no idempotency of their own — the OAuth
    reconnect callback and the "Corrigir sistema" button (which calls this
    once per active account). A duplicate tab, an F5, a double-click, or the
    OAuth provider retrying its callback can run this twice concurrently; two
    plain reads would both see the same ERROR jobs under READ COMMITTED and
    both dispatch them, risking a duplicate render/publish for the same job.
    """
    import os

    from sqlalchemy import or_, select, update

    from backend.database import SessionLocal
    from backend.models import JobStatus, VideoJob

    db = SessionLocal()
    resumed: list[int] = []
    to_publish: list[int] = []
    to_requeue: list[int] = []
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
            has_file = bool(job.main_video_path and os.path.exists(job.main_video_path))
            values = {"error_message": None, "approval_status": "approved"}
            if has_file:
                values["status"] = JobStatus.APPROVED   # render survives → publish only
            else:
                values["status"] = JobStatus.QUEUED      # file gone → must re-render
                values["progress"] = 0
                values["current_agent"] = None
            # ATOMIC claim: flip ERROR -> APPROVED/QUEUED only if STILL ERROR.
            # rowcount==0 means a concurrent call already claimed this job —
            # see the docstring above.
            claimed = db.execute(
                update(VideoJob)
                .where(VideoJob.id == job.id, VideoJob.status == JobStatus.ERROR)
                .values(**values)
                .execution_options(synchronize_session=False)
            ).rowcount
            db.commit()
            if not claimed:
                continue
            resumed.append(job.id)
            (to_publish if has_file else to_requeue).append(job.id)
        if resumed:
            logger.info("Reconexão da conta %s — %d job(s) retomado(s) automaticamente: %s",
                        account_id, len(resumed), resumed)
    except Exception:  # noqa: BLE001 — must never break the OAuth callback
        db.rollback()
        logger.exception("resume_account_blocked_jobs falhou (conta %s)", account_id)
        return resumed
    finally:
        db.close()
    # Dispatch AFTER the DB session closes, isolated per job — the same
    # pattern scheduler.py's stuck-job sweeps use, so a slow/failed dispatch
    # for one job can never prevent the others (already-committed) from
    # firing, or get mistaken for a claim failure.
    for job_id in to_publish:
        try:
            dispatch_publish(job_id)
        except Exception as exc:  # noqa: BLE001 — isolate per job
            logger.warning("resume_account_blocked_jobs: dispatch de publish falhou (job %s): %s", job_id, exc)
    for job_id in to_requeue:
        try:
            dispatch_job(job_id)
        except Exception as exc:  # noqa: BLE001 — isolate per job
            logger.warning("resume_account_blocked_jobs: dispatch de render falhou (job %s): %s", job_id, exc)
    return resumed


def resume_drive_blocked_jobs() -> list[int]:
    """The Drive connection was just reconnected with a fresh, working OAuth
    token — auto-resume every `from_ready_video` job parked because the SHARED
    Drive token had died (unlike a YouTube channel token, Drive's connection
    isn't scoped to one account_id, so this scans across all accounts).

    Requeues to QUEUED and re-dispatches via dispatch_job, which already routes
    `mode == "from_ready_video"` jobs to dispatch_retry_ready_video (see
    _job_mode below) — this re-downloads from Drive instead of regenerating
    via AI. Fire-and-forget, never blocks the OAuth callback.

    ATOMIC per-job claim (UPDATE ... WHERE status='error' + rowcount), same
    technique every periodic job in scheduler.py uses and the same reasoning
    as resume_account_blocked_jobs above: this is called from HTTP triggers
    (OAuth reconnect callback, "Corrigir sistema") with no idempotency of
    their own, so a duplicate/concurrent call must never dispatch the same
    job twice.
    """
    from sqlalchemy import or_, select, update

    from backend.database import SessionLocal
    from backend.models import JobStatus, VideoJob

    db = SessionLocal()
    resumed: list[int] = []
    try:
        rows = db.execute(
            select(VideoJob).where(
                VideoJob.mode == "from_ready_video",
                VideoJob.status == JobStatus.ERROR,
                or_(
                    VideoJob.error_message.contains("invalid_grant"),
                    VideoJob.error_message.contains("expired or revoked"),
                ),
            )
        ).scalars().all()
        for job in rows:
            # ATOMIC claim: flip ERROR -> QUEUED only if STILL ERROR.
            # rowcount==0 means a concurrent call already claimed this job.
            claimed = db.execute(
                update(VideoJob)
                .where(VideoJob.id == job.id, VideoJob.status == JobStatus.ERROR)
                .values(status=JobStatus.QUEUED, error_message=None, progress=0, current_agent=None)
                .execution_options(synchronize_session=False)
            ).rowcount
            db.commit()
            if not claimed:
                continue
            resumed.append(job.id)
        if resumed:
            logger.info("Reconexão do Drive — %d job(s) retomado(s) automaticamente: %s",
                        len(resumed), resumed)
    except Exception:  # noqa: BLE001 — must never break the OAuth callback
        db.rollback()
        logger.exception("resume_drive_blocked_jobs falhou")
        return resumed
    finally:
        db.close()
    for job_id in resumed:
        try:
            dispatch_job(job_id)
        except Exception as exc:  # noqa: BLE001 — isolate per job
            logger.warning("resume_drive_blocked_jobs: dispatch falhou (job %s): %s", job_id, exc)
    return resumed
