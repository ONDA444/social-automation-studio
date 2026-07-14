"""Analytics API — overview, per-job metrics, best times, thumbnail A/B."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.agents.analytics import AnalyticsAgent
from backend.agents.content_calendar import ContentCalendarAgent
from backend.agents.performance import PerformanceInsights
from backend.database import get_db
from backend.models import VideoAnalytics, VideoJob

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    """Totals across all videos.

    Counts ONLY the latest snapshot per (job, platform). Summing every snapshot row
    would triple-count each video (its 2h + 24h + 7d + live rows are the SAME video),
    so totals must collapse to one row per video per platform first."""
    totals = {"views": 0, "likes": 0, "comments": 0, "shares": 0}
    by_platform: dict[str, int] = {}
    jobs = db.execute(
        select(VideoJob).options(selectinload(VideoJob.analytics))
    ).scalars().all()
    for job in jobs:
        latest: dict[str, VideoAnalytics] = {}
        for r in (job.analytics or []):
            cur = latest.get(r.platform)
            if cur is None or (r.collected_at and (cur.collected_at is None
                               or r.collected_at > cur.collected_at)):
                latest[r.platform] = r
        for plat, r in latest.items():
            totals["views"] += int(r.views or 0)
            totals["likes"] += int(r.likes or 0)
            totals["comments"] += int(r.comments or 0)
            totals["shares"] += int(getattr(r, "shares", 0) or 0)
            by_platform[plat] = by_platform.get(plat, 0) + int(r.views or 0)
    return {"totals": totals, "by_platform": by_platform}


@router.get("/videos")
def videos(account_id: int | None = None, limit: int = 200, db: Session = Depends(get_db)):
    """Per-VIDEO breakdown (each published video separately, not aggregated).

    Returns one row per published job with its latest metrics summed across the
    platforms it was posted to. Videos with no metrics yet are still listed
    (zeros) so the user sees every video as soon as it publishes.
    """
    q = select(VideoJob).order_by(VideoJob.created_at.desc()).limit(limit)
    if account_id:
        q = select(VideoJob).where(VideoJob.account_id == account_id).order_by(
            VideoJob.created_at.desc()).limit(limit)
    out: list[dict] = []
    for job in db.execute(q).scalars().all():
        ps = job.publish_status or {}
        yt = ps.get("youtube") or {}
        published_anywhere = any(
            isinstance(p, dict) and (p.get("video_id") or p.get("url"))
            for p in ps.values()
        )
        if not published_anywhere:
            continue  # not actually published anywhere — skip
        latest: dict[str, VideoAnalytics] = {}
        for r in (job.analytics or []):
            cur = latest.get(r.platform)
            if cur is None or (r.collected_at and (cur.collected_at is None
                               or r.collected_at > cur.collected_at)):
                latest[r.platform] = r
        vals = latest.values()
        last_at = max((r.collected_at for r in vals if r.collected_at), default=None)
        out.append({
            "job_id": job.id,
            "title": job.title,
            "content_type": job.content_type,
            "format": getattr(job, "video_format", None),
            "account_id": job.account_id,
            "status": job.status.value if hasattr(job.status, "value") else job.status,
            "youtube_url": yt.get("url"),
            "views": sum(int(getattr(r, "views", 0) or 0) for r in vals),
            "likes": sum(int(getattr(r, "likes", 0) or 0) for r in vals),
            "comments": sum(int(getattr(r, "comments", 0) or 0) for r in vals),
            "snapshots": len(job.analytics or []),
            "last_collected": last_at.isoformat() if last_at else None,
            "published_at": job.updated_at.isoformat() if job.updated_at else None,
        })
    out.sort(key=lambda v: v["views"], reverse=True)
    return {"videos": out, "count": len(out)}


@router.get("/insights/{account_id}")
def insights(account_id: int, db: Session = Depends(get_db)):
    """The live 'what's working / what's not' brief that steers new videos."""
    return PerformanceInsights(db).account_insights(account_id)


@router.get("/job/{job_id}")
def job_analytics(job_id: int, db: Session = Depends(get_db)):
    rows = db.execute(
        select(VideoAnalytics).where(VideoAnalytics.job_id == job_id)
        .order_by(VideoAnalytics.collected_at)
    ).scalars().all()
    return {"job_id": job_id, "snapshots": [r.to_dict() for r in rows]}


@router.post("/collect/{job_id}")
def collect(job_id: int, snapshot_type: str = "2h", db: Session = Depends(get_db)):
    return {"collected": AnalyticsAgent(db).collect_for_job(job_id, snapshot_type)}


@router.get("/best-times/{account_id}")
def best_times(account_id: int, db: Session = Depends(get_db)):
    slots = ContentCalendarAgent(db).next_slots(account_id, 5, mode="smart")
    return {"account_id": account_id, "suggested": [s.isoformat() for s in slots]}


@router.get("/thumbnail-ab/{account_id}")
def thumbnail_ab(account_id: int, db: Session = Depends(get_db)):
    return AnalyticsAgent(db).thumbnail_ab_summary(account_id)
