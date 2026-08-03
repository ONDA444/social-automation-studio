"""
APScheduler periodic jobs (runs in the API process for local dev; in production
Celery beat can own these instead).

  - trending suggestions: 06:00 and 14:00
  - daily quota reset: 00:05
  - system heartbeat: every 30s
  - analytics collection: every 15 min (2h/24h/7d snapshots)
  - consume themes: every 2 min (generate videos ahead of their slot)
  - publish due: every 1 min (publish human-APPROVED videos when their slot arrives)

OWNERSHIP / MULTI-REPLICA WARNING
----------------------------------
This scheduler runs IN-PROCESS (APScheduler BackgroundScheduler), not as a
separate singleton service. If the API is ever deployed with more than one
replica/worker (e.g. `gunicorn -w N`, multiple Railway/K8s instances, or
horizontal autoscaling), EVERY replica that calls start_scheduler() will run
its OWN copy of every job below against the SAME database. That means
duplicate trending videos, duplicate quota resets, duplicate publishes, etc.
Several of the jobs above defend against this with atomic claim/rowcount
tricks (see _create_theme_job, _job_publish_due) precisely because this
single-owner assumption cannot be guaranteed at the code level.

The single source of truth for "should THIS process run the scheduler" is
the SCHEDULER_LEADER environment variable, read once below:
  - unset / "1" / "true"  -> this process starts the scheduler (default;
    matches historical behavior for single-replica/local-dev deployments).
  - "0" / "false"         -> this process is NOT the leader and must skip
    starting the scheduler entirely.

For a multi-replica production deployment, set SCHEDULER_LEADER=1 on exactly
ONE replica and SCHEDULER_LEADER=0 on all others. The long-term fix, already
flagged above, is to migrate these jobs to a real singleton (e.g. Celery beat
running as its own single-instance service, or a DB/Redis leader-election
lock) instead of relying on operators wiring env vars correctly per replica.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from backend.database import SessionLocal
from backend.events import publish_event

logger = logging.getLogger("studio.scheduler")
_scheduler: BackgroundScheduler | None = None
# Stamped by _job_heartbeat on every successful tick — the only proof the
# scheduler is genuinely still ticking, not just that BackgroundScheduler's
# thread object exists. A frozen/deadlocked scheduler thread would otherwise
# be invisible: nothing before this tracked "last time a job actually ran."
_last_heartbeat_at: datetime | None = None
# Heartbeat runs every 30s; 3 misses in a row is a real stall, not a GC pause.
_HEARTBEAT_STALE_AFTER = timedelta(seconds=90)


def _is_scheduler_leader() -> bool:
    """True unless SCHEDULER_LEADER is explicitly set to a falsy value.

    See the module docstring: in a multi-replica deployment only ONE process
    should have SCHEDULER_LEADER=1 (or unset); every other replica must set
    SCHEDULER_LEADER=0 so it never starts a duplicate in-process scheduler.
    """
    return os.getenv("SCHEDULER_LEADER", "1").strip().lower() not in ("0", "false", "no")


def start_scheduler() -> None:
    global _scheduler
    if _scheduler:
        return
    if not _is_scheduler_leader():
        logger.info(
            "Scheduler not started: SCHEDULER_LEADER=0 on this replica "
            "(only the designated leader replica runs periodic jobs)."
        )
        return
    sched = BackgroundScheduler(timezone="America/Sao_Paulo")
    sched.add_job(_job_trending, "cron", hour="6,14", id="trending", replace_existing=True)
    sched.add_job(_job_quota_reset, "cron", hour=0, minute=5, id="quota_reset", replace_existing=True)
    # next_run_time=now: without it, APScheduler's first interval tick is 30s
    # OUT, so scheduler_status()/get_system_health() would show a false "red"
    # (no heartbeat yet) for the first 30s after every boot/restart — including
    # right after ensure_scheduler_running() just fixed a dead scheduler.
    sched.add_job(_job_heartbeat, "interval", seconds=30, id="heartbeat", replace_existing=True,
                  next_run_time=datetime.now(timezone.utc))
    sched.add_job(_job_collect_analytics, "interval", minutes=15, id="analytics", replace_existing=True)
    # Near-real-time refresh: overwrite each published video's "live" snapshot with the
    # platform's CURRENT numbers so the Analytics tab matches YouTube/etc., not a frozen
    # 7-day count. Fires ~20s after boot, then every 8 min.
    # NOTE: next_run_time must be timezone-AWARE. A naive datetime is interpreted in the
    # scheduler's tz (America/Sao_Paulo), which would push the first run ~3h into the
    # future. An aware UTC datetime fires correctly ~20s after boot regardless of tz.
    sched.add_job(_job_refresh_live, "interval", minutes=8, id="analytics_live",
                  replace_existing=True,
                  next_run_time=datetime.now(timezone.utc) + timedelta(seconds=20))
    sched.add_job(_job_consume_themes, "interval", minutes=2, id="consume_themes", replace_existing=True)
    sched.add_job(_job_publish_due, "interval", minutes=1, id="publish_due", replace_existing=True)
    sched.add_job(_job_retry_errored, "interval", minutes=20, id="retry_errored", replace_existing=True)
    # Safety net for the in-process pipeline: a job stuck in PUBLISHING/PROCESSING
    # (e.g. a hung network call mid-upload) would otherwise sit there FOREVER since
    # _recover_orphan_jobs only runs once at boot — if the process never restarts,
    # nothing ever re-checks it, and it permanently occupies a publish semaphore slot.
    # This periodic sweep re-runs the SAME safe transition rules every 10 min.
    sched.add_job(_job_recover_stuck_publishing, "interval", minutes=10,
                  id="recover_stuck_publishing", replace_existing=True)
    # QUEUED is otherwise a state nothing periodic ever re-claims — see the
    # function's own docstring for the full chain of why. Confirmed in
    # production: 33 jobs stuck in QUEUED for up to 10 days with zero mechanism
    # that would ever touch them again.
    sched.add_job(_job_recover_stuck_queued, "interval", minutes=10,
                  id="recover_stuck_queued", replace_existing=True)
    # "Momento em alta": for opted-in channels, catch what's hot in the niche now and
    # enqueue 1–2 approval-gated videos. Every 3h (low/non-spammy); first run ~2min after boot.
    sched.add_job(_job_ride_trends, "interval", hours=3, id="ride_trends", replace_existing=True,
                  next_run_time=datetime.now(timezone.utc) + timedelta(minutes=2))
    # Catches the one failure mode upload_video() itself can never see: the
    # YouTube API accepted the upload (job marked PUBLISHED) but the video
    # never finishes server-side processing, staying "Pendente" in Studio
    # forever. Hourly is plenty — this is a many-hours-scale failure mode.
    sched.add_job(_job_check_stuck_youtube_processing, "interval", hours=1,
                  id="check_stuck_youtube_processing", replace_existing=True)
    sched.start()
    _scheduler = sched
    logger.info(
        "Scheduler started (trending, quota reset, heartbeat, analytics, "
        "consume_themes, publish_due, retry_errored, recover_stuck_publishing, "
        "recover_stuck_queued, check_stuck_youtube_processing)."
    )


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def scheduler_status() -> dict:
    """Liveness snapshot: is the in-process APScheduler actually running AND
    still ticking (not just instantiated)? Used by get_system_health() and by
    the 'fix everything' action to decide whether to restart it."""
    running = bool(_scheduler and _scheduler.running)
    stale = (
        _last_heartbeat_at is None
        or (datetime.utcnow() - _last_heartbeat_at) > _HEARTBEAT_STALE_AFTER
    )
    return {
        "leader": _is_scheduler_leader(),
        "running": running,
        "last_heartbeat_at": _last_heartbeat_at.isoformat() if _last_heartbeat_at else None,
        "alive": running and not stale,
    }


def ensure_scheduler_running() -> bool:
    """Best-effort self-heal for a dead/never-started scheduler: (re)starts it
    if this replica is the leader and it isn't already running. No-ops (and
    returns False) on a non-leader replica, matching start_scheduler's own
    ownership guard. Returns True if the scheduler is running after the call."""
    global _scheduler
    if _scheduler and _scheduler.running:
        return True
    _scheduler = None  # drop a dead/half-shutdown instance before restarting
    start_scheduler()
    return bool(_scheduler and _scheduler.running)


# ---- jobs ----
def _job_heartbeat() -> None:
    global _last_heartbeat_at
    try:
        from backend.agents.error_recovery import heartbeat

        heartbeat()
        _last_heartbeat_at = datetime.utcnow()
    except Exception as exc:  # noqa: BLE001
        logger.warning("heartbeat failed: %s", exc)


def _job_quota_reset() -> None:
    from sqlalchemy import select, update

    from backend.agents.account_profile import AccountProfileService
    from backend.models import JobStatus, VideoJob
    from backend.pipeline.dispatch import dispatch_publish

    db = SessionLocal()
    try:
        n = AccountProfileService(db).reset_daily_quota()
        held_ids = db.execute(
            select(VideoJob.id).where(VideoJob.status == JobStatus.AWAITING_QUOTA)
        ).scalars().all()
        resumed = []
        for job_id in held_ids:
            # ATOMIC claim: flip AWAITING_QUOTA -> APPROVED only if STILL
            # AWAITING_QUOTA. If another process (a second scheduler replica,
            # or a previous tick) already claimed it, rowcount==0 and we skip
            # — so the same job can never be dispatched twice.
            claimed = db.execute(
                update(VideoJob)
                .where(VideoJob.id == job_id, VideoJob.status == JobStatus.AWAITING_QUOTA)
                .values(status=JobStatus.APPROVED, error_message=None, approval_status="approved")
            ).rowcount
            db.commit()
            if claimed:
                resumed.append(job_id)
        for job_id in resumed:
            dispatch_publish(job_id)
        logger.info("Daily quota reset for %s accounts.", n)
        publish_event({"type": "quota_reset", "accounts": n, "resumed_jobs": resumed})
    finally:
        db.close()


# The ONLY failures we must NOT auto-retry: a publish interrupted mid-upload. The
# video may already be on the channel, so re-dispatching risks a DUPLICATE upload.
# These are parked in ERROR for a human (orphan recovery sets this message).
_NO_AUTO_RETRY_MARKERS = (
    "PODE já estar no canal",
    "Verifique o YouTube",
    # An interrupted render (prime OOM suspect) is parked for MANUAL Retry — never
    # auto-resurrect it, or a heavy/OOM render would crash the container every cycle.
    "Render interrompido",
    # Dead OAuth (revoked/expired refresh token): re-rendering the whole video from
    # scratch only to fail publish again burns the scarce free-LLM quota every cycle
    # — and that wasted burn is what surfaces as "scriptwriter esgotou (LLM indisponível)"
    # on OTHER jobs. Park auth failures until the user reconnects the channel; then new
    # jobs (or a manual Retry) flow normally.
    "invalid_grant",
    "expired or revoked",
    "credenciais conectadas",
    # A from_ready_video job that exhausted its Drive-download retry budget
    # (see _finalize_ready_video_job) already released its ready_video back to
    # the reservation pool for a FRESH job/attempt — resurrecting this exact
    # exhausted job too would let two attempts race for the same underlying
    # file again, the exact double-booking that produced duplicate
    # publishes of the same source video in production.
    "desistindo apos",
)

# Dispatches per tick, capped for the same reason _STUCK_QUEUED_BATCH_LIMIT
# caps _job_recover_stuck_queued: the in-process render pool has only 1-2
# slots (pipeline/dispatch.py's _MAX_RENDER). A provider-wide LLM outage can
# park dozens of jobs in the same evening, whose exponential backoffs then
# align and reopen on the SAME tick — dispatching all of them at once would
# be the same overload pattern that motivated _STUCK_QUEUED_BATCH_LIMIT,
# just via a different trigger.
_RETRY_ERRORED_BATCH_LIMIT = 5


def _job_retry_errored() -> None:
    """Resurrect videos that died on a transient LLM failure (free-tier 429 / daily
    quota window) so a scheduled post isn't lost forever. Resets them to QUEUED and
    re-dispatches; per-provider pacing + a reopened quota window usually let them
    through next time. Capped by retry_count (settings.llm_retry_max) so a genuinely
    broken job doesn't loop, and bounded to the last 24h so we never wake old ghosts.
    Dispatches are also capped per tick (_RETRY_ERRORED_BATCH_LIMIT) so a mass LLM
    failure can't dump dozens of jobs on the render pool the moment backoffs align.
    """
    from sqlalchemy import select, update

    from backend.config import settings
    from backend.models import JobStatus, VideoJob
    from backend.pipeline.dispatch import dispatch_job

    cap = settings.llm_retry_max
    if cap <= 0:
        return  # resurrection disabled
    # 72h window so an evening failure stays alive across the providers' real daily
    # quota reset (Groq/Gemini reset on US-Pacific midnight, NOT our 00:05 cron) and
    # absorbs scheduler restarts (Railway redeploy/OOM). 24h was too short.
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=72)

    db = SessionLocal()
    dispatched = 0
    try:
        # Over-fetch a bit: most candidates get filtered out below by the
        # marker exclusion or the backoff window, neither of which counts
        # toward the dispatch batch limit.
        rows = db.execute(
            select(VideoJob).where(
                VideoJob.status == JobStatus.ERROR,
                VideoJob.retry_count < cap,
                VideoJob.updated_at >= cutoff,
            )
            .order_by(VideoJob.updated_at.asc())
            .limit(_RETRY_ERRORED_BATCH_LIMIT * 6)
        ).scalars().all()
        for job in rows:
            if dispatched >= _RETRY_ERRORED_BATCH_LIMIT:
                break
            msg = job.error_message or ""
            # Auto-resurrect EVERY failed video back into production (user wants no
            # error left sitting), not only transient-LLM ones — EXCEPT a publish
            # interrupted mid-upload (duplicate-upload risk → left for a human).
            if any(m in msg for m in _NO_AUTO_RETRY_MARKERS):
                continue
            # Growing back-off between attempts: 30m, 60m, 120m, … capped at 6h. The
            # daily free quota doesn't reopen for hours, so retrying every 20m just
            # burns all attempts in <3h (same evening). Spacing them out spreads the
            # ~12 tries across ~40h so several land in DIFFERENT quota windows.
            min_gap_min = min(30 * (2 ** (job.retry_count or 0)), 360)
            if job.updated_at and (now - job.updated_at) < timedelta(minutes=min_gap_min):
                continue  # not yet time to retry this one
            new_retry_count = (job.retry_count or 0) + 1
            # ATOMIC claim: flip ERROR -> QUEUED only if STILL ERROR. If another
            # process (a second scheduler replica, or a previous tick) already
            # claimed/retried it, rowcount==0 and we skip — so the same job can
            # never be dispatched twice.
            claimed = db.execute(
                update(VideoJob)
                .where(VideoJob.id == job.id, VideoJob.status == JobStatus.ERROR)
                .values(
                    retry_count=new_retry_count,
                    status=JobStatus.QUEUED,
                    error_message=None,
                    current_agent=None,
                    progress=0,
                )
                # synchronize_session=False: force a single real UPDATE...WHERE
                # statement. The default "evaluate"/"fetch" sync strategies can
                # resolve matching rows via a separate SELECT and then update by
                # primary key alone, silently dropping the status=ERROR guard and
                # letting two racing replicas both "claim" the same job.
                .execution_options(synchronize_session=False)
            ).rowcount
            db.commit()
            if not claimed:
                continue  # someone else owns this retry — do not double-dispatch
            job.retry_count = new_retry_count
            dispatched += 1
            logger.info("Retry LLM-falho: job %s (tentativa %s/%s).", job.id, job.retry_count, cap)
            publish_event({
                "type": "job_update", "job_id": job.id, "status": "retry",
                "message": f"Reprocessando após falha de LLM (tentativa {job.retry_count}/{cap})",
            })
            try:
                dispatch_job(job.id)
            except Exception as exc:  # noqa: BLE001 — isolate per job
                logger.warning("retry_errored: dispatch failed for job %s: %s", job.id, exc)
    except Exception as exc:  # noqa: BLE001 — never let the scheduler die
        db.rollback()
        logger.warning("retry_errored job failed: %s", exc)
    finally:
        db.close()


# How long a job can sit in PUBLISHING/PROCESSING before we treat it as stuck.
# Long enough that a normal upload/render tick would have finished; short enough
# to unstick things quickly instead of starving the publish semaphore for hours.
_STUCK_JOB_MINUTES = 15


def _job_recover_stuck_publishing() -> None:
    """
    Runtime safety net (no restart required): the in-process pipeline has no
    separate worker watching PUBLISHING/PROCESSING jobs — _recover_orphan_jobs
    only runs once at boot. If the process itself doesn't die (e.g. a hung
    network call inside upload_video that never raises/times out), a job can
    sit in PUBLISHING forever, permanently occupying one of the (only 2)
    publish semaphore slots and starving every future publish.

    Every 10 min, find PUBLISHING/PROCESSING jobs whose updated_at is older
    than _STUCK_JOB_MINUTES and apply the SAME safe transition rules as boot
    recovery (backend.main._apply_orphan_transition) — never blindly
    re-publish a job that may have already uploaded. Jobs that come out as
    APPROVED (upload never started) are re-dispatched immediately for
    publish; jobs that come out as QUEUED (interrupted render, resumed once)
    are re-dispatched immediately for render — otherwise they'd sit QUEUED
    forever since nothing but the one-time boot redispatch
    (backend.main._redispatch_queued_jobs) ever picks up QUEUED jobs.

    A job that is merely SLOW (not dead — e.g. a real upload still grinding
    through a large file on a slow connection) must never be "recovered" and
    re-dispatched while its original task is still alive. Doing so used to
    spawn a second concurrent attempt for the same job, and with only 2
    publish slots, two such phantom duplicates alone exhausted the entire
    pool — the sweep meant to unstick the pipeline was creating a slower,
    self-renewing version of the exact deadlock it was built to fix.

    Two layers guard against that:
      1. dispatch.is_inflight() — an in-memory (or, with USE_CELERY, a
         Celery-broker) check. Correct within a single process/worker, but
         BLIND to any other replica: in a multi-replica deployment without
         Celery, a sibling web replica's sweep would see nothing "inflight"
         here even while this replica is genuinely mid-upload.
      2. The `updated_at < cutoff` filter itself, which is what actually makes
         this safe across replicas: agents/publisher.py runs a background
         heartbeat (_start_publish_heartbeat) that commits a fresh updated_at
         to the DB every ~60s for the entire duration of an in-flight upload,
         independent of which process/replica is doing the work. Because that
         signal is persisted (not in-memory), ANY replica's sweep reads the
         same up-to-date "still alive" state — a job is only ever considered
         stuck once its heartbeat has genuinely stopped.
    """
    from types import SimpleNamespace

    from sqlalchemy import select, update

    from backend.main import _apply_orphan_transition, _safe_boot
    from backend.models import JobStatus, VideoJob
    from backend.pipeline.dispatch import celery_inflight_ids, dispatch_job, dispatch_publish, is_inflight

    cutoff = datetime.utcnow() - timedelta(minutes=_STUCK_JOB_MINUTES)
    db = SessionLocal()
    try:
        stuck = db.execute(
            select(VideoJob).where(
                VideoJob.status.in_([JobStatus.PUBLISHING, JobStatus.PROCESSING]),
                VideoJob.updated_at < cutoff,
            )
        ).scalars().all()
        # One broker round-trip for the whole batch instead of one per
        # candidate — celery_inflight_ids() doesn't change mid-sweep, so
        # there's no reason to re-ask Celery for each job.
        celery_ids = celery_inflight_ids()
        stuck = [job for job in stuck if not is_inflight(job.id, celery_ids)]
        if not stuck:
            return
        safe = _safe_boot()
        to_publish = []
        to_requeue = []
        recovered = 0
        for job in stuck:
            was_publishing = job.status == JobStatus.PUBLISHING
            # Compute the transition on a DETACHED shadow copy, never on the
            # loaded ORM `job` — that instance can go stale between our SELECT
            # above and the claim below (e.g. the real publish path commits
            # status=PUBLISHED from another session in that window). Deciding
            # from stale state and then blind-committing it would silently
            # clobber a real PUBLISHED row and re-dispatch a duplicate upload.
            shadow = SimpleNamespace(
                status=job.status,
                publish_status=job.publish_status,
                orphan_resume_count=job.orphan_resume_count,
                error_message=job.error_message,
                current_agent=job.current_agent,
                progress=job.progress,
            )
            _apply_orphan_transition(shadow, safe)
            # ATOMIC claim: only apply the transition if the row's status is
            # STILL what we read it as. If another tick/replica already
            # recovered (or the real publish finished) in between, rowcount==0
            # and we skip — so the same job can never be dispatched twice.
            claimed = db.execute(
                update(VideoJob)
                .where(VideoJob.id == job.id, VideoJob.status == job.status)
                .values(
                    status=shadow.status,
                    error_message=shadow.error_message,
                    current_agent=shadow.current_agent,
                    progress=shadow.progress,
                    orphan_resume_count=shadow.orphan_resume_count,
                )
                .execution_options(synchronize_session=False)
            ).rowcount
            db.commit()
            if not claimed:
                continue  # lost the race — another replica/tick already handled it
            recovered += 1
            if was_publishing and shadow.status == JobStatus.APPROVED:
                to_publish.append(job.id)
            elif (not was_publishing) and shadow.status == JobStatus.QUEUED:
                to_requeue.append(job.id)
        logger.info(
            "Varredura de jobs presos: %d job(s) travado(s) em PUBLISHING/PROCESSING "
            "(> %dmin, sem tarefa viva) ajustado(s); %d reenviado(s) para publicação, "
            "%d reenviado(s) para render.",
            recovered, _STUCK_JOB_MINUTES, len(to_publish), len(to_requeue),
        )
    except Exception as exc:  # noqa: BLE001 — never let the scheduler die
        db.rollback()
        logger.warning("recover_stuck_publishing job failed: %s", exc)
        return
    finally:
        db.close()
    for job_id in to_publish:
        try:
            dispatch_publish(job_id)
        except Exception as exc:  # noqa: BLE001 — isolate per job
            logger.warning("recover_stuck_publishing: re-dispatch failed for job %s: %s", job_id, exc)
    for job_id in to_requeue:
        try:
            dispatch_job(job_id)
        except Exception as exc:  # noqa: BLE001 — isolate per job
            logger.warning("recover_stuck_publishing: re-dispatch (render) failed for job %s: %s", job_id, exc)


# QUEUED jobs found stale enough to redispatch (avoids racing a job that was
# JUST enqueued and hasn't been picked up by a worker loop yet).
_STUCK_QUEUED_MINUTES = 15
# Small on purpose: the in-process render pool has only 1-2 slots (see
# pipeline/dispatch.py's _MAX_RENDER). A batch of "everything at once" is
# exactly the overload from /jobs/fix-errors that stranded these jobs in the
# first place — sweeping the same way would just recreate it every 10 min.
_STUCK_QUEUED_BATCH_LIMIT = 3
# A job whose slot is older than this is deliberately NOT silently
# republished — see the docstring below.
_STUCK_QUEUED_MAX_AGE_HOURS = 24


def _job_recover_stuck_queued() -> None:
    """Runtime safety net for a QUEUED job nobody is ever going to pick up.

    Confirmed in production: 33 jobs sitting in QUEUED for up to 10 days, with
    zero mechanism that would ever touch them again. The reason: QUEUED is not
    actually owned by any periodic job. _job_publish_due only selects APPROVED
    (scheduler.py); _job_retry_errored only selects ERROR; the sweep right
    above this one (_job_recover_stuck_publishing) only selects PUBLISHING/
    PROCESSING. The one routine that DOES scan QUEUED —
    backend.main._redispatch_queued_jobs — only ever runs once, at process
    boot, and even then no-ops whenever USE_CELERY is true (which it always is
    in production). Meanwhile dispatch_job() for `from_ready_video` and
    `from_manual_upload` jobs ALWAYS runs in-process, never through Celery
    (pipeline/dispatch.py) — so those jobs are nobody's responsibility the
    moment the original in-memory dispatch is lost (a redeploy wipes the
    in-process queue; a burst from POST /jobs/fix-errors — which can flip many
    ERROR jobs to QUEUED in one call with no batch limit — overflows the
    single-slot render pool and strands the overflow in QUEUED forever).

    Every 10 min, this claims and redispatches a SMALL batch of QUEUED jobs
    whose updated_at is stale and that aren't already inflight
    (dispatch.is_inflight — cross-replica safe the same way
    _job_recover_stuck_publishing's is, since updated_at is the real signal,
    not the in-memory set alone). A job whose scheduled_at slot is more than
    _STUCK_QUEUED_MAX_AGE_HOURS in the past is deliberately NOT redispatched:
    silently catching up days of missed slots would publish a burst of stale
    content to the channel all at once. It's marked ERROR instead (releasing
    its reserved Drive file, if any, back to the pool) so a human decides
    whether to reschedule or delete it.
    """
    from sqlalchemy import select, update

    from backend.models import JobStatus, ReadyVideo, VideoJob
    from backend.pipeline.dispatch import celery_inflight_ids, dispatch_job, is_inflight

    cutoff = datetime.utcnow() - timedelta(minutes=_STUCK_QUEUED_MINUTES)
    max_age_cutoff = datetime.utcnow() - timedelta(hours=_STUCK_QUEUED_MAX_AGE_HOURS)
    db = SessionLocal()
    to_dispatch: list[int] = []
    expired = 0
    try:
        # Over-fetch a bit: some candidates will turn out inflight or expired,
        # neither of which counts toward the dispatch batch limit.
        candidates = db.execute(
            select(VideoJob)
            .where(VideoJob.status == JobStatus.QUEUED, VideoJob.updated_at < cutoff)
            .order_by(VideoJob.updated_at.asc())
            .limit(_STUCK_QUEUED_BATCH_LIMIT * 4)
        ).scalars().all()
        # One broker round-trip for the whole batch instead of one per
        # candidate (up to _STUCK_QUEUED_BATCH_LIMIT*4) — see the matching
        # comment in _job_recover_stuck_publishing above.
        celery_ids = celery_inflight_ids()
        for job in candidates:
            if is_inflight(job.id, celery_ids):
                continue
            if job.scheduled_at and job.scheduled_at < max_age_cutoff:
                ctx = job.video_context or {}
                ready_id = ctx.get("ready_video_id")
                if ready_id:
                    ready = db.get(ReadyVideo, ready_id)
                    if ready and ready.reserved_job_id == job.id:
                        ready.status = "available"
                        ready.reserved_job_id = None
                        ready.reserved_at = None
                job.status = JobStatus.ERROR
                job.error_message = (
                    "Slot agendado venceu há mais de 24h sem ser processado — não "
                    "republicado automaticamente para não postar conteúdo atrasado "
                    "em lote. Reagende ou exclua."
                )[:500]
                db.commit()
                expired += 1
                continue
            if len(to_dispatch) >= _STUCK_QUEUED_BATCH_LIMIT:
                continue
            # ATOMIC claim: flip QUEUED -> PROCESSING only if STILL QUEUED, and
            # commit PER JOB (not batched at the end) — same pattern as every
            # other claim in this file (_job_quota_reset, _job_retry_errored,
            # _job_recover_stuck_publishing, _create_theme_job). Bumping only
            # updated_at (the previous behavior) never actually changed status,
            # so a concurrent claim's WHERE status==QUEUED kept matching
            # indefinitely and two racing ticks/replicas could both "claim" and
            # dispatch the same job. Flipping status is what makes rowcount==0
            # for whoever loses the race.
            claimed = db.execute(
                update(VideoJob)
                .where(VideoJob.id == job.id, VideoJob.status == JobStatus.QUEUED)
                .values(status=JobStatus.PROCESSING, updated_at=datetime.utcnow())
                .execution_options(synchronize_session=False)
            ).rowcount
            db.commit()
            if claimed:
                to_dispatch.append(job.id)
        if expired or to_dispatch:
            logger.info(
                "Varredura de QUEUED presos: %d expirado(s) (slot > %dh vencido, "
                "marcado ERROR), %d redespachado(s).",
                expired, _STUCK_QUEUED_MAX_AGE_HOURS, len(to_dispatch),
            )
    except Exception as exc:  # noqa: BLE001 — never let the scheduler die
        db.rollback()
        logger.warning("recover_stuck_queued job failed: %s", exc)
        return
    finally:
        db.close()
    for job_id in to_dispatch:
        try:
            dispatch_job(job_id)
        except Exception as exc:  # noqa: BLE001 — isolate per job
            logger.warning("recover_stuck_queued: re-dispatch failed for job %s: %s", job_id, exc)


def _job_trending() -> None:
    from sqlalchemy import select

    from backend.agents.trending_agent import TrendingAgent
    from backend.models import PlatformAccount

    db = SessionLocal()
    try:
        accts = db.execute(select(PlatformAccount)).scalars().all()
        niches = {a.niche for a in accts if a.niche} or {"entretenimento"}
        for niche in niches:
            result = asyncio.run(TrendingAgent(job_id=None, emit=False).execute(niche=niche))
            publish_event({"type": "trending", "niche": niche, "suggestions": result["suggestions"]})
    except Exception as exc:  # noqa: BLE001
        logger.warning("trending job failed: %s", exc)
    finally:
        db.close()


def _job_collect_analytics() -> None:
    """Collect snapshots for jobs published ~2h / 24h / 7d ago."""
    from sqlalchemy import select

    from backend.agents.analytics import AnalyticsAgent
    from backend.models import JobStatus, VideoAnalytics, VideoJob

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        windows = {"2h": (2, 0.5), "24h": (24, 2), "7d": (168, 12)}
        # PUBLISHED is a terminal status (nothing transitions out of it), so
        # without a date filter this SELECT re-fetches every published job
        # EVER, in full — including the heavy JSON columns (script,
        # editing_plan, seo_metadata, style_dna; ~30-80KB each per
        # models/video_job.py) — every 15 min for the life of the deployment.
        # Bounded to the widest window + tolerance (168h + 12h = 180h) with a
        # margin: the same class of unbounded-growth OOM risk that
        # _job_refresh_live was capped for below.
        stale_cutoff = now - timedelta(hours=192)
        published = db.execute(
            select(VideoJob).where(
                VideoJob.status == JobStatus.PUBLISHED,
                VideoJob.updated_at >= stale_cutoff,
            )
        ).scalars().all()
        agent = AnalyticsAgent(db)
        collected = 0
        for job in published:
            age_h = (now - (job.updated_at or now)).total_seconds() / 3600
            for snap, (target_h, tol) in windows.items():
                if abs(age_h - target_h) <= tol:
                    exists = db.execute(
                        select(VideoAnalytics.id).where(
                            VideoAnalytics.job_id == job.id, VideoAnalytics.snapshot_type == snap
                        )
                    ).first()
                    if not exists:
                        agent.collect_for_job(job.id, snap)
                        collected += 1
                        # Live nudge so the Analytics tab refreshes the moment new
                        # numbers land, instead of waiting for the next poll.
                        publish_event({"type": "analytics_collected", "job_id": job.id,
                                       "account_id": job.account_id, "snapshot_type": snap})
        # Heartbeat so the UI can show a fresh "ao vivo" timestamp every cycle even
        # when no new snapshot was due this tick.
        publish_event({"type": "analytics_tick", "collected": collected})
    except Exception as exc:  # noqa: BLE001
        logger.warning("analytics job failed: %s", exc)
    finally:
        db.close()


# Every published job used to be refreshed EVERY tick (8 min), unbounded — fine
# with a handful of videos, but with hundreds of published jobs each iteration
# builds fresh Google API client objects (cache_discovery=False -> no reuse) and
# fires 2+ HTTP calls (a near-guaranteed 401-then-refresh, then the real request,
# doubled again when with_analytics kicks in for videos >=24h old). Confirmed in
# production: this scaled into a burst of 1000+ HTTP calls/objects in a single
# synchronous tick as the catalog grew, and the web service was OOM-killed by the
# platform mid-burst (clean "Stopping Container" with zero app-level exception —
# the signature of an external SIGKILL, not a crash). Cap the batch and rotate by
# least-recently-refreshed so every video still gets covered over time, just
# spread across ticks instead of one unbounded burst.
_REFRESH_LIVE_BATCH_LIMIT = 40


def _job_refresh_live() -> None:
    """Overwrite each published video's 'live' snapshot with current platform numbers
    so the Analytics page reflects near-real-time views, not a frozen snapshot."""
    from sqlalchemy import and_, select
    from sqlalchemy.orm import aliased

    from backend.agents.analytics import AnalyticsAgent
    from backend.models import JobStatus, VideoAnalytics, VideoJob

    db = SessionLocal()
    try:
        live_snap = aliased(VideoAnalytics)
        published = db.execute(
            select(VideoJob)
            .outerjoin(
                live_snap,
                and_(live_snap.job_id == VideoJob.id, live_snap.snapshot_type == "live"),
            )
            .where(VideoJob.status == JobStatus.PUBLISHED)
            .order_by(live_snap.collected_at.asc().nullsfirst())
            .limit(_REFRESH_LIVE_BATCH_LIMIT)
        ).scalars().all()
        agent = AnalyticsAgent(db)
        # Batched: one videos().list/reports().query call per chunk of up to 50
        # video IDs (grouped by owning account) instead of one call per job —
        # see AnalyticsAgent.refresh_live_batch. Same quota per chunk as per
        # single ID, so this is what actually cuts the HTTP-call volume behind
        # the OOM burst _REFRESH_LIVE_BATCH_LIMIT above caps the symptom of.
        try:
            results = agent.refresh_live_batch([job.id for job in published])
        except Exception as exc:  # noqa: BLE001
            logger.warning("live refresh batch failed: %s", exc)
            results = {}
        refreshed = 0
        for job in published:
            if results.get(job.id):
                refreshed += 1
                publish_event({"type": "analytics_collected", "job_id": job.id,
                               "account_id": job.account_id, "snapshot_type": "live"})
        publish_event({"type": "analytics_tick", "collected": refreshed, "live": True})
    except Exception as exc:  # noqa: BLE001
        logger.warning("live refresh job failed: %s", exc)
    finally:
        db.close()


def _create_trending_job(db, acct, moment) -> int:
    """Create a QUEUED, AUTO-PUBLISHING VideoJob from a trending moment, then dispatch.

    Moments are time-sensitive, so (unlike normal channel videos) these publish
    DIRECTLY — require_approval=False lets the orchestrator auto-publish when
    AUTO_PUBLISH is on. It still rides the SAME pipeline (scriptwriter + the channel's
    learning/performance brief + the real-script-only rule), so the video is concrete
    and on-brand, never generic. The is_trending marker drives the '🔥 do momento'
    badge and the 6h idempotency check."""
    from backend.agents.scriptwriter import ScriptwriterAgent
    from backend.models import JobStatus, VideoJob
    from backend.pipeline.dispatch import dispatch_job

    # Pick a content_type that fits the moment (football → sports_highlights, etc.)
    # instead of forcing film_recap on everything.
    try:
        content_type = ScriptwriterAgent._detect_content_type(f"{moment['title']} {moment.get('topic', '')}")
    except Exception:  # noqa: BLE001
        content_type = "film_recap_ai_images"

    # SAFETY: a trending video auto-publishes — the only thing that routes it to human
    # Approval is BRAND SAFETY (tragedy/death/politics/violence). Grounding is a QUALITY
    # concern, not an approval one: the scriptwriter already retries when the LLM is down
    # and the quality gate blocks generic/off-topic scripts, so an ungrounded-but-clean
    # moment should still publish directly rather than pile up in Approvals.
    safe = bool(moment.get("safe", True))
    require_approval = not safe

    job = VideoJob(
        title=moment["title"],
        topic=moment.get("topic") or moment["title"],
        content_type=content_type,
        video_format="short",                   # moments are punchy → short by default
        target_platforms=["youtube"],
        account_id=acct.id,
        status=JobStatus.QUEUED,
        scheduled_at=None,                       # publish ASAP (it's a moment)
        video_context={
            "require_approval": require_approval,  # brand-safe → direct; sensitive → Approval
            "is_trending": True,
            "trend_source": moment.get("source", "google_news"),
            "trend_evidence": moment.get("evidence", ""),
        },
    )
    db.add(job)
    db.flush()
    db.commit()
    dispatch_job(job.id)
    return job.id


# How long a video may sit accepted-but-not-yet-processed on YouTube's side
# before we treat it as permanently stuck rather than "still transcoding".
# Real transcodes finish in minutes to a couple hours even for long videos;
# this is deliberately generous to never flag a merely-slow one.
_STUCK_PROCESSING_HOURS = 24
# Real HTTP calls per tick, capped for the same reason _REFRESH_LIVE_BATCH_LIMIT
# caps _job_refresh_live: this loop makes synchronous googleapiclient calls in
# series, so a heavy publishing day (dozens of candidates in the recency
# window) could otherwise chain that many blocking requests on a single tick.
_STUCK_YT_CHECK_BATCH_LIMIT = 25
# Hard ceiling on a SINGLE status check, mirroring publisher.py's
# _UPLOAD_TIMEOUT_S. uploaders.youtube._service() already gives httplib2 a 30s
# per-socket-op timeout, but that bounds one connect/read, not the call as a
# whole (retries/slow multi-packet reads can still add up) — this is the outer
# backstop so one bad video can never hang the scheduler thread.
_STUCK_YT_CHECK_CALL_TIMEOUT_S = 45


def _job_check_stuck_youtube_processing() -> None:
    """Catches the one failure mode upload_video() itself can never see: the
    YouTube API accepted the upload (an insert() response with a video_id,
    so the job was marked PUBLISHED) but the video never finishes YouTube's
    own server-side transcode pipeline — it sits as "Pendente" in Studio
    forever. Because nothing about the upload call itself failed, this is
    completely invisible to every status our own pipeline tracks; confirmed
    in production as ~170 videos silently accumulated this way across 2
    channels with zero errors ever appearing in the Fila.

    Checks each recently-published YouTube video's real processing status
    exactly once (`processing_checked` flag, so this never re-checks a video
    it already resolved) and records the result on the job's publish_status.
    A job flagged `stuck_processing` still shows as PUBLISHED (the upload
    genuinely happened — flipping it to ERROR here could trigger the retry
    machinery into re-rendering and re-uploading ANOTHER copy, the exact bug
    this sweep exists to catch) but the flag itself is what a human — or a
    future UI surface — uses to know it needs a manual look.

    Bounded to _STUCK_YT_CHECK_BATCH_LIMIT real API calls per tick (hourly, so
    the rest simply get picked up on a later run) and each call is bounded to
    _STUCK_YT_CHECK_CALL_TIMEOUT_S so a single stalled connection can never
    hang this sweep for the rest of the tick.
    """
    from sqlalchemy import select

    from backend.agents.account_profile import AccountProfileService
    from backend.models import JobStatus, VideoJob
    from backend.uploaders.youtube import get_video_processing_status

    now = datetime.utcnow()
    # Only look at a bounded recent window: skip videos so old that YouTube
    # processing failing/succeeding either way no longer matters operationally,
    # and skip videos published so recently that "still processing" is normal.
    window_start = now - timedelta(hours=_STUCK_PROCESSING_HOURS * 4)
    window_end = now - timedelta(hours=_STUCK_PROCESSING_HOURS)

    db = SessionLocal()
    try:
        # Over-fetch a bit: candidates already `processing_checked` or missing
        # credentials are skipped for free (no HTTP call) and don't count
        # toward the batch limit below.
        candidates = db.execute(
            select(VideoJob)
            .where(
                VideoJob.status == JobStatus.PUBLISHED,
                VideoJob.account_id.is_not(None),
                VideoJob.updated_at >= window_start,
                VideoJob.updated_at <= window_end,
            )
            .order_by(VideoJob.updated_at.asc())
            .limit(_STUCK_YT_CHECK_BATCH_LIMIT * 3)
        ).scalars().all()
        svc = AccountProfileService(db)
        checked = 0
        stuck = 0
        attempts = 0
        for job in candidates:
            if attempts >= _STUCK_YT_CHECK_BATCH_LIMIT:
                break
            pub = job.publish_status or {}
            yt = pub.get("youtube") or {}
            video_id = yt.get("video_id")
            if not video_id or yt.get("processing_checked"):
                continue
            creds = svc.get_credentials(job.account_id)
            if not creds:
                continue
            attempts += 1
            try:
                info = asyncio.run(asyncio.wait_for(
                    asyncio.to_thread(get_video_processing_status, video_id, creds),
                    timeout=_STUCK_YT_CHECK_CALL_TIMEOUT_S,
                ))
            except asyncio.TimeoutError:
                logger.warning(
                    "check_stuck_youtube_processing: timeout (>%ss) checando video %s (job %s).",
                    _STUCK_YT_CHECK_CALL_TIMEOUT_S, video_id, job.id,
                )
                continue  # try again next hour
            if info.get("found") is not True:
                continue  # API error or video not found — try again next hour
            checked += 1
            new_pub = dict(pub)
            new_yt = dict(yt)
            new_yt["processing_checked"] = True
            new_yt["upload_status_seen"] = info.get("upload_status")
            if info.get("upload_status") != "processed":
                new_yt["stuck_processing"] = True
                stuck += 1
            new_pub["youtube"] = new_yt
            job.publish_status = new_pub
            db.commit()
        if checked:
            logger.info(
                "Verificação de processamento no YouTube: %d checado(s), %d ainda travado(s) após %dh.",
                checked, stuck, _STUCK_PROCESSING_HOURS,
            )
    except Exception as exc:  # noqa: BLE001 — never let the scheduler die
        db.rollback()
        logger.warning("check_stuck_youtube_processing falhou: %s", exc)
    finally:
        db.close()


def _job_ride_trends() -> None:
    """'Momento em alta': for each opted-in active channel, find the hottest niche
    moments now and enqueue up to `trends_per_cycle` (max 2) approval-gated videos.
    Guarded so it never floods: opt-in only, ≤2/cycle, and a 6h idempotency window."""
    import asyncio as _aio
    from datetime import timedelta as _td

    from sqlalchemy import select

    from backend.agents.account_profile import AccountProfileService
    from backend.agents.trending_moment import TrendingMomentAgent
    from backend.models import PlatformAccount, ThemeQueue, VideoJob

    db = SessionLocal()
    from backend.config import settings

    if not settings.trending_enabled:
        return  # global kill-switch — halt all trending auto-publishing instantly
    try:
        now = datetime.utcnow()
        profile = AccountProfileService(db)
        accounts = db.execute(
            select(PlatformAccount).where(
                PlatformAccount.ride_trends.is_(True),
                PlatformAccount.status == "active",
            )
        ).scalars().all()
        for acct in accounts:
            try:
                if getattr(acct, "video_source_mode", "ai") == "drive":
                    continue
                # Don't render a moment the channel can't even upload (quota/creds).
                try:
                    if not profile.can_upload(acct.id):
                        continue
                except Exception:  # noqa: BLE001
                    pass
                per_cycle = min(2, max(1, getattr(acct, "trends_per_cycle", 1) or 1))
                recent_jobs = db.execute(
                    select(VideoJob).where(VideoJob.account_id == acct.id)
                    .order_by(VideoJob.created_at.desc()).limit(30)
                ).scalars().all()
                trending_recent = [j for j in recent_jobs if (j.video_context or {}).get("is_trending")]
                # Don't stack moments: skip if we already made a trending video <6h ago.
                if any(j.created_at and (now - j.created_at) < _td(hours=6) for j in trending_recent):
                    continue
                # Daily cap: never auto-publish more than max_trending_per_day per channel.
                made_today = sum(1 for j in trending_recent
                                 if j.created_at and (now - j.created_at) < _td(hours=24))
                if made_today >= settings.max_trending_per_day:
                    continue
                recent_titles = [j.title for j in recent_jobs if j.title]
                pend = db.execute(
                    select(ThemeQueue.theme).where(
                        ThemeQueue.account_id == acct.id, ThemeQueue.status == "pending")
                ).scalars().all()
                recent_titles += [t for t in pend if t]

                lang = acct.content_language or "pt-BR"
                region = "BR" if lang.lower().startswith("pt") else "US"
                result = _aio.run(TrendingMomentAgent(job_id=None, emit=False).execute(
                    niche=acct.niche or "entretenimento", language=lang, region=region,
                    max_moments=per_cycle, recent_titles=recent_titles,
                ))
                created = 0
                for moment in (result.get("moments") or [])[:per_cycle]:
                    _create_trending_job(db, acct, moment)
                    created += 1
                if created:
                    publish_event({"type": "trending_moment", "account_id": acct.id, "created": created})
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.debug("ride_trends account %s failed: %s", acct.id, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ride_trends job failed: %s", exc)
    finally:
        db.close()


def _as_naive_utc(dt: datetime) -> datetime:
    """
    Persist scheduled_at as NAIVE UTC.

    next_slots() returns tz-aware UTC datetimes; the VideoJob.scheduled_at column
    is a naive DateTime that holds UTC wall-clock. Stripping tzinfo here keeps the
    publish-due comparison (scheduled_at <= utcnow()) apples-to-apples and avoids
    'can't compare offset-naive and offset-aware datetimes' errors.
    """
    from datetime import timezone

    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _slots_due_today(hhmm_times: list[str], per_day: int, tz_name: str | None, now_utc: datetime) -> int:
    """
    How many of today's posting slots have already arrived (slot time <= now),
    capped at videos_per_day.

    `hhmm_times` are the EFFECTIVE HH:MM slots (already resolved for fixed/smart
    mode by the calendar) in the account's local timezone. We compare against the
    local wall-clock 'now'. This is what paces generation: one video is produced
    only once its slot has come — never the whole list up front (which would
    hammer the LLM/voice APIs).
    """
    from datetime import time as _time
    from zoneinfo import ZoneInfo

    per_day = max(1, per_day or 1)
    try:
        tz = ZoneInfo(tz_name or "America/Sao_Paulo")
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("America/Sao_Paulo")
    times = []
    for hhmm in (hhmm_times or ["19:00"]):
        try:
            h, m = str(hhmm).split(":")
            times.append(_time(int(h), int(m)))
        except Exception:  # noqa: BLE001
            continue
    times = sorted(times)[:per_day]
    now_local = now_utc.astimezone(tz)
    today = now_local.date()
    due = 0
    for t in times:
        slot_local = datetime.combine(today, t, tzinfo=tz)
        if slot_local <= now_local:
            due += 1
    return due


def _create_theme_job(db, account_id: int, theme, scheduled_naive: datetime, slot_key: str | None = None) -> int:
    """Create a QUEUED VideoJob from a theme, mark the theme consumed, dispatch it.

    `slot_key` (schedule/publish-ahead callers only — immediate mode passes None)
    is written to VideoJob.schedule_slot_key, which carries a DB unique index
    (see database.ensure_indexes). That turns the caller's slot-clash SELECT
    from check-then-act into a real claim: if a second process raced past the
    same SELECT for this slot, its insert here hits IntegrityError and loses.
    """
    from sqlalchemy import update as _update
    from sqlalchemy.exc import IntegrityError

    from backend.models import JobStatus, PlatformAccount, ThemeQueue, VideoJob
    from backend.pipeline.dispatch import dispatch_job

    # ATOMIC claim: flip pending -> consumed only if STILL pending. If another
    # process (e.g. a second scheduler replica) already claimed this theme,
    # rowcount==0 and we bail out — preventing two VideoJobs for one theme.
    claimed = db.execute(
        _update(ThemeQueue)
        .where(ThemeQueue.id == theme.id, ThemeQueue.status == "pending")
        .values(status="consumed")
    ).rowcount
    db.commit()
    if not claimed:
        return 0
    theme.status = "consumed"

    acct = db.get(PlatformAccount, account_id)
    source_mode = getattr(acct, "video_source_mode", "ai") if acct else "ai"
    if source_mode in {"drive", "mixed"} and acct is not None:
        ready_job_id = _try_create_ready_video_job(db, acct, scheduled_naive, theme=theme, slot_key=slot_key)
        if ready_job_id:
            return ready_job_id
        if source_mode == "drive":
            logger.info("Drive sem video compativel para conta %s; tema %s permanece pendente.", account_id, theme.id)
            # No ready video available — release the claim so the theme stays
            # pending (matches the log message) instead of being stuck "consumed".
            theme.status = "pending"
            db.commit()
            return 0

    job = VideoJob(
        title=theme.theme,
        topic=theme.theme,
        content_type=theme.content_type or "film_recap_ai_images",
        video_format=getattr(theme, "video_format", "long") or "long",
        target_platforms=theme.target_platforms or ["youtube"],
        account_id=account_id,
        status=JobStatus.QUEUED,
        scheduled_at=scheduled_naive,
        schedule_slot_key=slot_key,
    )
    db.add(job)
    try:
        db.flush()
    except IntegrityError:
        # Another process already claimed this slot between our clash-check
        # SELECT and this insert. Release the theme claim back to pending and
        # bail out instead of double-booking the slot.
        db.rollback()
        theme.status = "pending"
        db.commit()
        return 0
    theme.consumed_job_id = job.id
    db.commit()
    dispatch_job(job.id)
    return job.id


def _try_create_ready_video_job(
    db, acct, scheduled_naive: datetime, theme=None, slot_key: str | None = None
) -> int | None:
    """Reserve a Drive ready-video and create a publishable VideoJob."""
    from sqlalchemy.exc import IntegrityError

    from backend.agents.drive_library import DriveLibraryService
    from backend.models import JobStatus, VideoJob

    drive = DriveLibraryService(db)
    theme_text = (getattr(theme, "theme", None) or "").strip()
    content_type = getattr(theme, "content_type", None) or "film_recap_ai_images"
    requested_format = getattr(theme, "video_format", None) or "long"
    ready = drive.reserve_next(
        acct,
        content_type=content_type,
        video_format=requested_format,
        fallback_format="long" if requested_format == "short" else None,
    )
    if not ready:
        return None

    title = (theme_text or _clean_ready_title(ready.name) or "Video pronto").strip()
    video_format = ready.video_format or requested_format or "long"
    job = VideoJob(
        title=title[:300],
        topic=theme_text or ready.name,
        mode="from_ready_video",
        content_type=content_type,
        video_format=video_format,
        target_platforms=(getattr(theme, "target_platforms", None) or ["youtube"]),
        account_id=acct.id,
        status=JobStatus.AWAITING_APPROVAL,
        approval_status="pending",
        scheduled_at=scheduled_naive,
        progress=100,
        current_agent="drive_library",
        video_context={
            "source": "drive_ready_video",
            "ready_video_id": ready.id,
            "drive_file_id": ready.drive_file_id,
            "drive_name": ready.name,
            "drive_folder_path": ready.folder_path,
            "niche": ready.niche or acct.drive_niche or acct.niche,
        },
        qc_status="skipped_ready_video",
        compliance_status="needs_rights_review",
        schedule_slot_key=slot_key,
    )
    db.add(job)
    try:
        db.flush()
    except IntegrityError:
        # Another process already claimed this slot between the caller's
        # clash-check SELECT and this insert. drive.reserve_next()'s ReadyVideo
        # claim above is uncommitted in this same transaction, so rolling back
        # here also releases it for the next reservation attempt.
        db.rollback()
        return None
    ready.reserved_job_id = job.id
    _finalize_ready_video_job(db, drive, job, ready, acct, title_seed=title, video_format=video_format, theme=theme)
    return job.id


# A ready_video is released back to the shared reservation pool only after
# this many failed attempts on the SAME job (see the except-block below) —
# not on every failure. See that block's comment for why this matters.
_READY_VIDEO_MAX_ATTEMPTS = 2


def _finalize_ready_video_job(
    db, drive, job, ready, acct, *, title_seed: str, video_format: str, theme=None
) -> None:
    """Download the reserved Drive file, package SEO/thumbnail, and move the
    job to its terminal pre-publish state (or back to ERROR on failure).

    Shared by `_try_create_ready_video_job` (first attempt) and
    `retry_ready_video_job` (the only safe retry path for a `from_ready_video`
    job — see dispatch.py's `_job_mode` routing in dispatch_job) so the two
    paths can't silently drift apart."""
    from backend.agents.ready_video_curation import apply_curation_layer
    from backend.agents.ready_video_seo import build_ready_video_package, is_audio_ready, render_audio_track_as_video
    from backend.config import settings
    from backend.models import JobStatus

    # Which step we're on, so the except-block below can name the REAL failing
    # stage. Before this, an ffmpeg failure while wrapping a music track was
    # reported to the user as "Falha ao baixar video do Drive" (and
    # error_messages.py then told them it was a network/permission problem) —
    # actively misleading, and it sent a whole debugging session down the
    # wrong path chasing Drive permissions.
    stage = "baixar video do Drive"
    try:
        local_path = drive.download_for_job(ready, job.id)
        if is_audio_ready(ready.name, ready.mime_type):
            # A music-only Drive niche has no video to publish — wrap the
            # track into a real MP4 (static cover + audio) so it can go out
            # like any other ready_video. See ready_video_seo.py for why.
            stage = "gerar video a partir do audio"
            local_path = render_audio_track_as_video(
                job.id, local_path, title_seed, ready.niche or acct.drive_niche or acct.niche or "",
                video_format,
            )
            # The job's content_type was set at creation time from the
            # requested theme (e.g. "film_recap_ai_images") before we knew
            # the reserved Drive file was audio-only. Left uncorrected, the
            # video published on YouTube Category 24 "Entertainment" instead
            # of 10 "Music", and inherited "recap de filme" titles/tags for
            # what is actually a song — confirmed in production. "music" is
            # an internal-only marker (not in CONTENT_TYPE_KEYS): it only
            # drives category/title/tag template lookups below, it never
            # goes through the AI script-generation pipeline that requires
            # TEMPLATE_GUIDE/HEURISTICS entries.
            job.content_type = "music"
        stage = "preparar o video do Drive"
        job.main_video_path = local_path
        if video_format == "short":
            job.shorts_paths = [local_path]
        analysis, seo, thumbnail_path = build_ready_video_package(
            job_id=job.id,
            local_path=local_path,
            ready=ready,
            account=acct,
            content_type=job.content_type,
            video_format=video_format,
            title_seed=title_seed,
        )
        job.seo_metadata = seo
        if thumbnail_path:
            job.thumbnail_path = thumbnail_path
        yt_title = ((seo.get("youtube") or {}).get("title") or title_seed).strip()
        if yt_title:
            job.title = yt_title[:300]
            job.topic = yt_title
        ctx = dict(job.video_context or {})
        ctx["content_analysis"] = analysis
        ctx["seo_source"] = (analysis or {}).get("analysis_source") or "fallback"
        job.video_context = ctx
        if analysis.get("duration"):
            ready.duration_seconds = int(float(analysis.get("duration") or 0))
        if analysis.get("aspect_ratio") == "9:16":
            ready.video_format = "short"
            job.video_format = "short"
            job.shorts_paths = [local_path]
        elif analysis.get("aspect_ratio") == "16:9" and video_format != "short":
            ready.video_format = "long"
            job.video_format = "long"
            job.shorts_paths = None

        # Editorial curation layer (see ready_video_curation.py for the full why):
        # adds an original spoken take (long) or on-screen commentary line (short)
        # so every Drive publish carries real editorial value instead of a bare
        # re-upload -- exactly what ONDA444's "conteudo reutilizado" rejection was
        # missing. Skipped for wrapped music tracks (not the reused-footage risk
        # this targets). Best-effort: apply_curation_layer never raises and falls
        # back to the untouched clip on any failure, so it can never block a publish.
        if job.content_type != "music":
            stage = "aplicar camada de curadoria"
            curation_format = job.video_format or video_format
            # A Channel row is optional (see models/channel.py) -- when the
            # account has one, its visual_theme/tts_voice give this publish
            # the channel's own identity instead of the fixed yellow-on-black
            # default every channel used to share.
            from backend.models import Channel as _Channel

            channel = (
                db.query(_Channel).filter_by(account_id=acct.id).first() if acct else None
            )
            curated_path = apply_curation_layer(
                job_id=job.id,
                local_path=local_path,
                analysis=analysis,
                context={
                    "niche": getattr(ready, "niche", None) or getattr(acct, "drive_niche", None)
                    or getattr(acct, "niche", None) or "",
                    "account_niche": getattr(acct, "niche", "") or "",
                },
                video_format=curation_format,
                visual_theme=channel.resolved_visual_theme() if channel else None,
                voice=channel.tts_voice if channel else None,
                intro_mode=channel.intro_mode if channel else None,
                channel_id=channel.id if channel else None,
            )
            if curated_path != local_path:
                local_path = curated_path
                job.main_video_path = local_path
                if curation_format == "short":
                    job.shorts_paths = [local_path]
        stage = "preparar o video do Drive"
        meta = dict(ready.metadata_json or {})
        meta["analysis"] = {
            k: v
            for k, v in (analysis or {}).items()
            if k not in {"title_options"} and not (k == "warnings" and not v)
        }
        ready.metadata_json = meta
        # Drive ready-videos never need human approval — it's the user's own
        # pre-made file, nothing here is AI-generated. This is unconditional
        # (not gated on settings.auto_publish, which only governs the AI/manual
        # flows) so a Drive job never sits waiting in AWAITING_APPROVAL.
        job.approval_status = "approved"
        if job.scheduled_at is None:
            job.scheduled_at = datetime.utcnow()
        if (settings.publish_mode or "schedule").lower() == "schedule":
            job.status = JobStatus.PUBLISHING
        else:
            job.status = JobStatus.APPROVED
        if theme is not None:
            theme.status = "consumed"
            theme.consumed_job_id = job.id
        db.commit()
        if (settings.publish_mode or "schedule").lower() == "schedule":
            from backend.pipeline.dispatch import dispatch_publish

            dispatch_publish(job.id)
    except Exception as exc:  # noqa: BLE001
        job.status = JobStatus.ERROR
        # Dedicated counter, NOT retry_count: retry_count is also bumped by
        # scheduler._job_retry_errored's unrelated LLM-failure resurrection
        # sweep (which treats every ERROR job, including this one, as
        # eligible) and by the manual Retry button — sharing it would let
        # those unrelated bumps exhaust this budget before the job ever made
        # _READY_VIDEO_MAX_ATTEMPTS real download attempts. See
        # models/video_job.py's ready_video_attempt_count for the same
        # reasoning that split off orphan_resume_count from retry_count.
        job.ready_video_attempt_count = (job.ready_video_attempt_count or 0) + 1
        # Confirmed in production: releasing the ready_video back to
        # "available" on EVERY failure let a totally different theme/job grab
        # the SAME Drive file minutes later via _try_create_ready_video_job,
        # while THIS failed job also stayed eligible for its own retry
        # (retry_ready_video_job) — two independent attempts at the same
        # source file, sometimes both eventually succeeding and publishing
        # the same video twice under different generated titles. Cap the
        # retries on THIS job first (mirrors _ORPHAN_RESUME_MAX's "try once
        # more, then stop" elsewhere in this codebase); only release the file
        # for a fresh attempt once this job has genuinely given up, and mark
        # it with a phrase in _NO_AUTO_RETRY_MARKERS so this exhausted job is
        # never ALSO auto-resurrected alongside that fresh attempt.
        if job.ready_video_attempt_count >= _READY_VIDEO_MAX_ATTEMPTS:
            ready.status = "available"
            ready.reserved_job_id = None
            ready.reserved_at = None
            job.error_message = (
                f"Falha ao {stage}, desistindo apos {job.ready_video_attempt_count} "
                f"tentativas: {exc}"
            )[:500]
        else:
            job.error_message = f"Falha ao {stage}: {exc}"[:500]
        db.commit()
        logger.warning("ready video job failed for account %s: %s", getattr(acct, "id", None), exc)


def retry_ready_video_job(job_id: int) -> None:
    """The ONLY safe retry for a `from_ready_video` job that landed in ERROR
    (e.g. a transient Drive download failure).

    dispatch_job()'s generic retry path (manual Retry button, and the 72h
    error-resurrection sweep in `_job_retry_errored`) runs the full AI
    orchestrator — which would silently write a brand-new AI-scripted video
    (TTS narration + generated images) into a job whose whole point was to
    publish the user's own pre-made Drive file, keyed only by coincidentally
    reusing the job's title/topic as the AI's topic. `dispatch.py`'s
    `_job_mode` routing in dispatch_job redirects every re-dispatch path for
    a `from_ready_video` job here instead of the full orchestrator.
    """
    from backend.agents.drive_library import DriveLibraryService
    from backend.models import JobStatus, PlatformAccount, ReadyVideo, VideoJob

    db = SessionLocal()
    try:
        job = db.get(VideoJob, job_id)
        if not job:
            return
        ready_video_id = (job.video_context or {}).get("ready_video_id")
        ready = db.get(ReadyVideo, ready_video_id) if ready_video_id else None
        if ready is None:
            job.status = JobStatus.ERROR
            job.error_message = "Video original do Drive nao foi encontrado para nova tentativa."
            db.commit()
            return
        acct = db.get(PlatformAccount, job.account_id) if job.account_id else None
        video_format = job.video_format or ready.video_format or "long"
        drive = DriveLibraryService(db)
        _finalize_ready_video_job(db, drive, job, ready, acct, title_seed=job.title, video_format=video_format)
    finally:
        db.close()


def _clean_ready_title(name: str | None) -> str:
    import re

    value = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", name or "").strip()
    value = re.sub(r"[_-]+", " ", value)
    # Strip a bare sequence number in parens (e.g. "Academia (35)") — this is
    # how the same Drive folder disambiguates same-named files, but this
    # value becomes the public YouTube title verbatim (title_seed wins
    # ready_video_seo.py::_best_topic's priority order), so an un-stripped
    # "(35)" was visibly exposing "episode N of a mass-produced series" to
    # viewers on every video from these folders — confirmed in production as
    # correlated with the worst-performing channels. ready_video_seo.py's own
    # _clean_filename already strips this for its (lower-priority) fallback
    # path; this brings the higher-priority title_seed path in line with it.
    value = re.sub(r"\(\s*\d+\s*\)", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:100]


def _ready_video_seo(title: str, ready, acct, content_type: str, video_format: str) -> dict:
    from backend.agents.ready_video_seo import build_drive_seo
    from backend.agents.seo_agent import apply_runtime_youtube_enrichment_sync

    context = {
        "title_seed": title,
        "drive_name": getattr(ready, "name", "") or "",
        "folder_path": getattr(ready, "folder_path", "") or "",
        "niche": getattr(ready, "niche", None) or getattr(acct, "drive_niche", None) or getattr(acct, "niche", None) or "",
        "account_niche": getattr(acct, "niche", "") or "",
        "display_name": getattr(acct, "display_name", "") or "",
        "target_audience": getattr(acct, "target_audience", "") or "",
        "content_type": content_type,
        "video_format": video_format,
    }
    seo = build_drive_seo(context=context, analysis={})
    return apply_runtime_youtube_enrichment_sync(seo)


def _job_consume_themes() -> None:
    """
    Turn pending themes into videos AT their scheduled slot time — one per slot,
    in theme order — instead of generating the whole list up front.

    For each ACTIVE account with a ScheduleConfig and pending themes:
      budget = (slots already due today) - (videos already generated today)
    We consume exactly `budget` themes now (usually 1, right after a slot ticks
    over), create the VideoJob, and dispatch generation. With AUTO_PUBLISH on the
    orchestrator approves+publishes it automatically when the render finishes;
    otherwise it lands in Approvals. This natural pacing keeps API usage low and
    spreads posts across the day.
    """
    from datetime import timezone as _tz

    from sqlalchemy import func, select

    from backend.agents.content_calendar import ContentCalendarAgent
    from backend.config import settings
    from backend.models import JobStatus, PlatformAccount, ScheduleConfig, ThemeQueue, VideoJob
    from backend.pipeline.dispatch import dispatch_job

    now_utc = datetime.now(_tz.utc)

    db = SessionLocal()
    try:
        calendar = ContentCalendarAgent(db)
        accounts = db.execute(
            select(PlatformAccount).where(PlatformAccount.status == "active")
        ).scalars().all()

        for acct in accounts:
            try:
                cfg = db.execute(
                    select(ScheduleConfig).where(ScheduleConfig.account_id == acct.id)
                ).scalars().first()
                if cfg is None:
                    logger.debug("Conta %s sem ScheduleConfig — automacao desativada (sem agenda salva).", acct.id)
                    continue  # no schedule configured -> account opts out of automation
                source_mode = getattr(acct, "video_source_mode", "ai")

                pending = db.execute(
                    select(ThemeQueue)
                    .where(
                        ThemeQueue.account_id == acct.id,
                        ThemeQueue.status == "pending",
                    )
                    .order_by(ThemeQueue.position.asc(), ThemeQueue.created_at.asc())
                ).scalars().all()
                if not pending and source_mode != "drive":
                    logger.debug("Conta %s (%s) sem temas na fila — nada a gerar neste ciclo.",
                                 acct.id, source_mode)
                    continue

                per_day = max(1, cfg.videos_per_day or 1)
                from zoneinfo import ZoneInfo
                try:
                    tz = ZoneInfo(cfg.timezone or "America/Sao_Paulo")
                except Exception:  # noqa: BLE001
                    tz = ZoneInfo("America/Sao_Paulo")

                if (settings.publish_mode or "schedule").lower() == "schedule":
                    # Generate AHEAD of each upcoming slot and upload to YouTube as
                    # SCHEDULED (publishAt = slot). One job per slot, deduped by the
                    # job's scheduled_at so we never double-book a slot.
                    from datetime import timedelta
                    lead = timedelta(minutes=max(5, settings.generation_lead_minutes))
                    win = timedelta(minutes=5)
                    pidx = 0
                    for slot in calendar.upcoming_slots(acct.id, cfg, per_day, now_utc):
                        if slot > now_utc + lead:
                            break  # sorted — later slots aren't due to generate yet
                        slot_naive = slot.astimezone(_tz.utc).replace(tzinfo=None)
                        clash = db.execute(
                            select(VideoJob.id).where(
                                VideoJob.account_id == acct.id,
                                VideoJob.scheduled_at >= slot_naive - win,
                                VideoJob.scheduled_at <= slot_naive + win,
                            )
                        ).first()
                        if clash:
                            continue  # already generated for this slot
                        theme = None
                        if pidx < len(pending):
                            theme = pending[pidx]
                            pidx += 1
                        elif source_mode != "drive":
                            break
                        # Backstop for the SELECT-based clash check above: passed down
                        # to a DB-unique column so a second process racing this same
                        # slot loses atomically at insert time instead of also passing
                        # the (non-locking) clash SELECT and double-booking the slot.
                        slot_key = f"{acct.id}:{slot_naive.isoformat()}"
                        jid = (
                            _create_theme_job(db, acct.id, theme, slot_naive, slot_key=slot_key)
                            if theme is not None
                            else _try_create_ready_video_job(db, acct, slot_naive, slot_key=slot_key)
                        )
                        if jid:
                            logger.info("Agendado: %s -> job %s (conta %s, publica %s UTC).",
                                        f"theme {theme.id}" if theme is not None else "Drive ready video",
                                        jid, acct.id, slot_naive)
                else:
                    # Immediate mode: generate AT the slot, publish public right away.
                    times = calendar.resolve_post_times(acct.id, cfg, per_day)
                    due = _slots_due_today(times, per_day, cfg.timezone, now_utc)
                    if due <= 0:
                        continue
                    midnight_local = datetime.combine(now_utc.astimezone(tz).date(),
                                                      datetime.min.time(), tzinfo=tz)
                    midnight_naive_utc = midnight_local.astimezone(_tz.utc).replace(tzinfo=None)
                    for idx in range(int(due)):
                        # Re-read the live count on every iteration (instead of computing
                        # a single "budget" up front) so a concurrent scheduler replica's
                        # just-committed VideoJob rows for this account/day are visible
                        # before we decide to create another — this closes the TOCTOU
                        # window down to a single count-then-create per job instead of
                        # per tick.
                        generated_today = db.execute(
                            select(func.count(VideoJob.id)).where(
                                VideoJob.account_id == acct.id,
                                VideoJob.created_at >= midnight_naive_utc,
                            )
                        ).scalar() or 0
                        if generated_today >= due:
                            break
                        theme = pending[idx] if idx < len(pending) else None
                        if theme is None and source_mode != "drive":
                            break
                        jid = (
                            _create_theme_job(db, acct.id, theme, now_utc.replace(tzinfo=None))
                            if theme is not None
                            else _try_create_ready_video_job(db, acct, now_utc.replace(tzinfo=None))
                        )
                        if jid:
                            logger.info("Slot due -> gerando %s como job %s (conta %s).",
                                        f"theme {theme.id}" if theme is not None else "Drive ready video",
                                        jid, acct.id)
            except Exception as exc:  # noqa: BLE001 — per-account isolation
                db.rollback()
                logger.warning("consume_themes failed for account %s: %s", acct.id, exc)
    except Exception as exc:  # noqa: BLE001 — never let the scheduler die
        logger.warning("consume_themes job failed: %s", exc)
    finally:
        db.close()


def _job_publish_due() -> None:
    """
    Publish videos whose scheduled slot has arrived — but ONLY those a human has
    already APPROVED. Picks APPROVED jobs with scheduled_at <= now(UTC) and hands
    each to dispatch_publish. Jobs still AWAITING_APPROVAL are intentionally
    skipped (the human gate is sacred).
    """
    from sqlalchemy import select, update

    from backend.models import JobStatus, VideoJob
    from backend.pipeline.dispatch import dispatch_publish

    db = SessionLocal()
    try:
        now = datetime.utcnow()  # naive UTC, matches the stored scheduled_at column
        due = db.execute(
            select(VideoJob.id, VideoJob.scheduled_at).where(
                VideoJob.status == JobStatus.APPROVED,
                VideoJob.scheduled_at.isnot(None),
                VideoJob.scheduled_at <= now,
            )
        ).all()
        for job_id, sched in due:
            try:
                # ATOMIC claim: flip APPROVED -> PUBLISHING only if STILL APPROVED.
                # If another path (the orchestrator's auto-publish, a previous tick, or
                # a second process) already claimed/published it, rowcount==0 and we
                # skip — so the same video can never be published twice. run_publish
                # accepts PUBLISHING; orphan recovery handles it safely on restart.
                claimed = db.execute(
                    update(VideoJob)
                    .where(VideoJob.id == job_id, VideoJob.status == JobStatus.APPROVED)
                    .values(status=JobStatus.PUBLISHING)
                ).rowcount
                db.commit()
                if not claimed:
                    continue  # someone else owns this publish — do not double-dispatch
                dispatch_publish(job_id)
                logger.info("Publishing due job %s (scheduled %s UTC).", job_id, sched)
            except Exception as exc:  # noqa: BLE001 — isolate per job
                db.rollback()
                logger.warning("publish_due failed for job %s: %s", job_id, exc)
    except Exception as exc:  # noqa: BLE001 — never let the scheduler die
        logger.warning("publish_due job failed: %s", exc)
    finally:
        db.close()
