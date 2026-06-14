"""Dashboard API — summary metrics, system health, trending suggestions."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.agents.error_recovery import get_system_health
from backend.agents.trending_agent import TrendingAgent
from backend.database import get_db
from backend.models import PlatformAccount, VideoAnalytics, VideoJob

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
def overview(db: Session = Depends(get_db)):
    status_counts = dict(
        db.execute(select(VideoJob.status, func.count()).group_by(VideoJob.status)).all()
    )
    recent = db.execute(select(VideoJob).order_by(VideoJob.created_at.desc()).limit(8)).scalars().all()
    total_views = db.execute(select(func.coalesce(func.sum(VideoAnalytics.views), 0))).scalar() or 0
    accounts = db.execute(select(func.count()).select_from(PlatformAccount)).scalar() or 0
    return {
        "status_counts": {(k.value if hasattr(k, "value") else k): v for k, v in status_counts.items()},
        "recent_jobs": [j.to_dict() for j in recent],
        "total_views": int(total_views),
        "accounts": accounts,
        "active_jobs": sum(v for k, v in status_counts.items()
                           if (k.value if hasattr(k, "value") else k) in ("queued", "processing", "publishing")),
    }


@router.get("/health")
def health():
    return get_system_health()


@router.get("/trending")
async def trending(niche: str = Query("entretenimento")):
    agent = TrendingAgent(job_id=None, emit=False)
    return await agent.execute(niche=niche)
