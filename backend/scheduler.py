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
from datetime import datetime, timedelta

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
    sched.add_job(_job_consume_themes, "interval", minutes=2, id="consume_themes", replace_existing=True)
    sched.add_job(_job_publish_due, "interval", minutes=1, id="publish_due", replace_existing=True)
    sched.start()
    _scheduler = sched
    logger.info(
        "Scheduler started (trending, quota reset, heartbeat, analytics, "
        "consume_themes, publish_due)."
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
    except Exception as exc:  # noqa: BLE001
        logger.debug("analytics job failed: %s", exc)
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

                # Slots that have arrived today, minus what we've already generated
                # today for this account = how many to kick off right now. Times are
                # resolved through the calendar so "smart" mode (best/learned hours)
                # paces generation the same way the user sees it on the Agenda.
                per_day = max(1, cfg.videos_per_day or 1)
                times = calendar.resolve_post_times(acct.id, cfg, per_day)
                due = _slots_due_today(times, per_day, cfg.timezone, now_utc)
                if due <= 0:
                    continue  # next slot hasn't arrived yet — wait

                # Local midnight (account tz) expressed as naive UTC, to match the
                # naive-UTC created_at column.
                from zoneinfo import ZoneInfo
                try:
                    tz = ZoneInfo(cfg.timezone or "America/Sao_Paulo")
                except Exception:  # noqa: BLE001
                    tz = ZoneInfo("America/Sao_Paulo")
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
                    continue  # this slot already produced its video

                take = min(budget, len(pending))
                for theme in pending[:take]:
                    job = VideoJob(
                        title=theme.theme,
                        topic=theme.theme,
                        content_type=theme.content_type or "film_recap_ai_images",
                        video_format=getattr(theme, "video_format", "long") or "long",
                        target_platforms=theme.target_platforms or ["youtube"],
                        account_id=acct.id,
                        status=JobStatus.QUEUED,
                        scheduled_at=now_utc.replace(tzinfo=None),  # slot is due -> publish now
                    )
                    db.add(job)
                    db.flush()  # assign job.id before we reference it

                    theme.status = "consumed"
                    theme.consumed_job_id = job.id
                    db.commit()

                    dispatch_job(job.id)
                    logger.info(
                        "Slot due -> gerando theme %s como job %s (conta %s).",
                        theme.id, job.id, acct.id,
                    )
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
    from sqlalchemy import select

    from backend.models import JobStatus, VideoJob
    from backend.pipeline.dispatch import dispatch_publish

    db = SessionLocal()
    try:
        now = datetime.utcnow()  # naive UTC, matches the stored scheduled_at column
        due = db.execute(
            select(VideoJob).where(
                VideoJob.status == JobStatus.APPROVED,
                VideoJob.scheduled_at.isnot(None),
                VideoJob.scheduled_at <= now,
            )
        ).scalars().all()
        for job in due:
            try:
                # Flip to PUBLISHING *before* dispatching so the next tick (60s) does
                # NOT re-select this still-APPROVED job while it sits in the serial
                # worker queue — that caused the same video to publish 2x+. run_publish
                # accepts PUBLISHING; orphan recovery resets it to APPROVED on restart.
                job.status = JobStatus.PUBLISHING
                db.commit()
                dispatch_publish(job.id)
                logger.info("Publishing due job %s (scheduled %s UTC).", job.id, job.scheduled_at)
            except Exception as exc:  # noqa: BLE001 — isolate per job
                db.rollback()
                logger.warning("publish_due failed for job %s: %s", job.id, exc)
    except Exception as exc:  # noqa: BLE001 — never let the scheduler die
        logger.warning("publish_due job failed: %s", exc)
    finally:
        db.close()
