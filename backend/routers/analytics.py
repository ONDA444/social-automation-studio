"""Analytics API — overview, per-job metrics, best times, thumbnail A/B."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.agents.analytics import AnalyticsAgent
from backend.agents.content_calendar import ContentCalendarAgent
from backend.database import get_db
from backend.models import VideoAnalytics

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    agg = db.execute(
        select(
            func.coalesce(func.sum(VideoAnalytics.views), 0),
            func.coalesce(func.sum(VideoAnalytics.likes), 0),
            func.coalesce(func.sum(VideoAnalytics.comments), 0),
            func.coalesce(func.sum(VideoAnalytics.shares), 0),
        )
    ).one()
    by_platform = db.execute(
        select(VideoAnalytics.platform, func.coalesce(func.sum(VideoAnalytics.views), 0))
        .group_by(VideoAnalytics.platform)
    ).all()
    return {
        "totals": {"views": int(agg[0]), "likes": int(agg[1]),
                   "comments": int(agg[2]), "shares": int(agg[3])},
        "by_platform": {p: int(v) for p, v in by_platform},
    }


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
