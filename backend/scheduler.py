"""
APScheduler periodic jobs (runs in the API process for local dev; in production
Celery beat can own these instead).

  - trending suggestions: 06:00 and 14:00
  - daily quota reset: 00:05
  - system heartbeat: every 30s
  - analytics collection: every 15 min (2h/24h/7d snapshots)
  - consume themes: every 2 min (generate videos ahead of their slot)
  - publish due: every 1 min (publish human-APPROVED videos when their slot arrives)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from backend.database import SessionLocal
from backend.events import publish_event

logger = logging.getLogger("studio.scheduler")
_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> None:
    global _scheduler
    if _scheduler:
        return
    sched = BackgroundScheduler(timezone="America/Sao_Paulo")
    sched.add_job(_job_trending, "cron", hour="6,14", id="trending", replace_existing=True)
    sched.add_job(_job_quota_reset, "cron", hour=0, minute=5, id="quota_reset", replace_existing=True)
    sched.add_job(_job_heartbeat, "interval", seconds=30, id="heartbeat", replace_existing=True)
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
    # "Momento em alta": for opted-in channels, catch what's hot in the niche now and
    # enqueue 1–2 approval-gated videos. Every 3h (low/non-spammy); first run ~2min after boot.
    sched.add_job(_job_ride_trends, "interval", hours=3, id="ride_trends", replace_existing=True,
                  next_run_time=datetime.now(timezone.utc) + timedelta(minutes=2))
    sched.start()
    _scheduler = sched
    logger.info(
        "Scheduler started (trending, quota reset, heartbeat, analytics, "
        "consume_themes, publish_due, retry_errored)."
    )


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


# ---- jobs ----
def _job_heartbeat() -> None:
    try:
        from backend.agents.error_recovery import heartbeat

        heartbeat()
    except Exception as exc:  # noqa: BLE001
        logger.debug("heartbeat failed: %s", exc)


def _job_quota_reset() -> None:
    from backend.agents.account_profile import AccountProfileService

    db = SessionLocal()
    try:
        n = AccountProfileService(db).reset_daily_quota()
        logger.info("Daily quota reset for %s accounts.", n)
        publish_event({"type": "quota_reset", "accounts": n})
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
)

# The ONLY failures we must NOT auto-retry: a publish interrupted mid-upload. The
# video may already be on the channel, so re-dispatching risks a DUPLICATE upload.
# These are parked in ERROR for a human (orphan recovery sets this message).
_NO_AUTO_RETRY_MARKERS = (
    "PODE já estar no canal",
    "Verifique o YouTube",
)


def _job_retry_errored() -> None:
    """Resurrect videos that died on a transient LLM failure (free-tier 429 / daily
    quota window) so a scheduled post isn't lost forever. Resets them to QUEUED and
    re-dispatches; per-provider pacing + a reopened quota window usually let them
    through next time. Capped by retry_count (settings.llm_retry_max) so a genuinely
    broken job doesn't loop, and bounded to the last 24h so we never wake old ghosts.
    """
    from sqlalchemy import select

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
    try:
        rows = db.execute(
            select(VideoJob).where(
                VideoJob.status == JobStatus.ERROR,
                VideoJob.retry_count < cap,
                VideoJob.updated_at >= cutoff,
            )
        ).scalars().all()
        for job in rows:
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
            job.retry_count = (job.retry_count or 0) + 1
            job.status = JobStatus.QUEUED
            job.error_message = None
            job.current_agent = None
            job.progress = 0
            db.commit()
            logger.info("Retry LLM-falho: job %s (tentativa %s/%s).", job.id, job.retry_count, cap)
            publish_event({
                "type": "job_update", "job_id": job.id, "status": "retry",
                "message": f"Reprocessando após falha de LLM (tentativa {job.retry_count}/{cap})",
            })
            dispatch_job(job.id)
    except Exception as exc:  # noqa: BLE001 — never let the scheduler die
        db.rollback()
        logger.warning("retry_errored job failed: %s", exc)
    finally:
        db.close()


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
        logger.debug("trending job failed: %s", exc)
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
        published = db.execute(
            select(VideoJob).where(VideoJob.status == JobStatus.PUBLISHED)
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
        logger.debug("analytics job failed: %s", exc)
    finally:
        db.close()


def _job_refresh_live() -> None:
    """Overwrite each published video's 'live' snapshot with current platform numbers
    so the Analytics page reflects near-real-time views, not a frozen snapshot."""
    from sqlalchemy import select

    from backend.agents.analytics import AnalyticsAgent
    from backend.models import JobStatus, VideoJob

    db = SessionLocal()
    try:
        published = db.execute(
            select(VideoJob).where(VideoJob.status == JobStatus.PUBLISHED)
        ).scalars().all()
        agent = AnalyticsAgent(db)
        refreshed = 0
        for job in published:
            try:
                if agent.refresh_live(job.id):
                    refreshed += 1
                    publish_event({"type": "analytics_collected", "job_id": job.id,
                                   "account_id": job.account_id, "snapshot_type": "live"})
            except Exception as exc:  # noqa: BLE001
                logger.debug("live refresh for job %s failed: %s", job.id, exc)
        publish_event({"type": "analytics_tick", "collected": refreshed, "live": True})
    except Exception as exc:  # noqa: BLE001
        logger.debug("live refresh job failed: %s", exc)
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
        logger.debug("ride_trends job failed: %s", exc)
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


def _create_theme_job(db, account_id: int, theme, scheduled_naive: datetime) -> int:
    """Create a QUEUED VideoJob from a theme, mark the theme consumed, dispatch it."""
    from backend.models import JobStatus, PlatformAccount, VideoJob
    from backend.pipeline.dispatch import dispatch_job

    acct = db.get(PlatformAccount, account_id)
    source_mode = getattr(acct, "video_source_mode", "ai") if acct else "ai"
    if source_mode in {"drive", "mixed"} and acct is not None:
        ready_job_id = _try_create_ready_video_job(db, acct, theme, scheduled_naive)
        if ready_job_id:
            return ready_job_id
        if source_mode == "drive":
            logger.info("Drive sem video compativel para conta %s; tema %s permanece pendente.", account_id, theme.id)
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
    )
    db.add(job)
    db.flush()
    theme.status = "consumed"
    theme.consumed_job_id = job.id
    db.commit()
    dispatch_job(job.id)
    return job.id


def _try_create_ready_video_job(db, acct, theme, scheduled_naive: datetime) -> int | None:
    """Reserve a Drive ready-video and create a publishable VideoJob."""
    import asyncio

    from backend.agents.drive_library import DriveLibraryService
    from backend.agents.seo_agent import SEOAgent
    from backend.config import settings
    from backend.models import JobStatus, VideoJob

    drive = DriveLibraryService(db)
    ready = drive.reserve_next(
        acct,
        content_type=theme.content_type or "film_recap_ai_images",
        video_format=getattr(theme, "video_format", "long") or "long",
        fallback_format="long" if (getattr(theme, "video_format", "long") or "long") == "short" else None,
    )
    if not ready:
        return None

    title = (theme.theme or ready.name or "Video pronto").strip()
    video_format = ready.video_format or getattr(theme, "video_format", "long") or "long"
    job = VideoJob(
        title=title[:300],
        topic=theme.theme,
        mode="from_ready_video",
        content_type=theme.content_type or "film_recap_ai_images",
        video_format=video_format,
        target_platforms=theme.target_platforms or ["youtube"],
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
    )
    db.add(job)
    db.flush()
    ready.reserved_job_id = job.id
    try:
        local_path = drive.download_for_job(ready, job.id)
        job.main_video_path = local_path
        if video_format == "short":
            job.shorts_paths = [local_path]
        script_stub = {
            "title": title,
            "content_type": job.content_type,
            "seo_keywords": [v for v in [ready.niche, acct.niche, title] if v],
            "scenes": [],
        }
        ctx = {
            "target_platforms": job.target_platforms or ["youtube"],
            "script": script_stub,
            "niche": ready.niche or acct.niche,
        }
        try:
            job.seo_metadata = asyncio.run(
                SEOAgent(job_id=job.id, context=ctx, emit=False).execute(
                    script=script_stub,
                    narration={},
                    content_type=job.content_type,
                    language=getattr(acct, "content_language", None),
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("SEO para ready video job %s caiu no basico: %s", job.id, exc)
            job.seo_metadata = {
                "youtube": {
                    "title": title[:100],
                    "description": f"{title}\n\nVideo pronto selecionado da biblioteca do canal.",
                    "tags": [t for t in [ready.niche, acct.niche, "video"] if t],
                    "category_id": "22",
                },
                "tiktok": {"caption": f"{title} #fyp #viral"[:150]},
                "instagram": {"caption": title, "hashtags": ["#reels", "#viral"]},
            }
        if settings.auto_publish:
            job.approval_status = "approved"
            if job.scheduled_at is None:
                job.scheduled_at = datetime.utcnow()
            if (settings.publish_mode or "schedule").lower() == "schedule":
                job.status = JobStatus.PUBLISHING
            else:
                job.status = JobStatus.APPROVED
        theme.status = "consumed"
        theme.consumed_job_id = job.id
        db.commit()
        if settings.auto_publish and (settings.publish_mode or "schedule").lower() == "schedule":
            from backend.pipeline.dispatch import dispatch_publish

            dispatch_publish(job.id)
        return job.id
    except Exception as exc:  # noqa: BLE001
        ready.status = "error"
        job.status = JobStatus.ERROR
        job.error_message = f"Falha ao baixar video do Drive: {exc}"[:500]
        db.commit()
        logger.warning("ready video job failed for account %s: %s", acct.id, exc)
        return job.id


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
                    continue  # no schedule configured -> account opts out of automation

                pending = db.execute(
                    select(ThemeQueue)
                    .where(
                        ThemeQueue.account_id == acct.id,
                        ThemeQueue.status == "pending",
                    )
                    .order_by(ThemeQueue.position.asc(), ThemeQueue.created_at.asc())
                ).scalars().all()
                if not pending:
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
                        if pidx >= len(pending):
                            break
                        theme = pending[pidx]
                        pidx += 1
                        jid = _create_theme_job(db, acct.id, theme, slot_naive)
                        if jid:
                            logger.info("Agendado: theme %s -> job %s (conta %s, publica %s UTC).",
                                        theme.id, jid, acct.id, slot_naive)
                else:
                    # Immediate mode: generate AT the slot, publish public right away.
                    times = calendar.resolve_post_times(acct.id, cfg, per_day)
                    due = _slots_due_today(times, per_day, cfg.timezone, now_utc)
                    if due <= 0:
                        continue
                    midnight_local = datetime.combine(now_utc.astimezone(tz).date(),
                                                      datetime.min.time(), tzinfo=tz)
                    midnight_naive_utc = midnight_local.astimezone(_tz.utc).replace(tzinfo=None)
                    generated_today = db.execute(
                        select(func.count(VideoJob.id)).where(
                            VideoJob.account_id == acct.id,
                            VideoJob.created_at >= midnight_naive_utc,
                        )
                    ).scalar() or 0
                    budget = int(due) - int(generated_today)
                    if budget <= 0:
                        continue
                    for theme in pending[:min(budget, len(pending))]:
                        jid = _create_theme_job(db, acct.id, theme, now_utc.replace(tzinfo=None))
                        if jid:
                            logger.info("Slot due -> gerando theme %s como job %s (conta %s).",
                                        theme.id, jid, acct.id)
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
