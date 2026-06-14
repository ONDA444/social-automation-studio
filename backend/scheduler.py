"""
APScheduler periodic jobs (runs in the API process for local dev; in production
Celery beat can own these instead).

  - trending suggestions: 06:00 and 14:00
  - daily quota reset: 00:05
  - system heartbeat: every 30s
  - analytics collection: every 15 min (2h/24h/7d snapshots)
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
    sched.start()
    _scheduler = sched
    logger.info("Scheduler started (trending, quota reset, heartbeat, analytics).")


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
