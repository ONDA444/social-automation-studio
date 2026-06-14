"""Workspaces API — per-account view (queue, quota, stats, linking)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.agents.account_profile import AccountProfileService
from backend.models import JobStatus, VideoJob

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("")
def list_workspaces(db: Session = Depends(get_db)):
    svc = AccountProfileService(db)
    out = []
    for acct in svc.list():
        counts = dict(
            db.execute(
                select(VideoJob.status, func.count())
                .where(VideoJob.account_id == acct.id)
                .group_by(VideoJob.status)
            ).all()
        )
        out.append({
            "account": acct.to_dict(),
            "queue_counts": {(k.value if hasattr(k, "value") else k): v for k, v in counts.items()},
            "quota_remaining": acct.quota_limit - acct.quota_used_today,
        })
    return {"workspaces": out}


@router.get("/{account_id}")
def get_workspace(account_id: int, db: Session = Depends(get_db)):
    svc = AccountProfileService(db)
    acct = svc.get(account_id)
    if not acct:
        raise HTTPException(404, "conta não encontrada")
    jobs = svc.get_workspace_queue(account_id)
    awaiting = sum(1 for j in jobs if j.status == JobStatus.AWAITING_APPROVAL)
    published = sum(1 for j in jobs if j.status == JobStatus.PUBLISHED)
    return {
        "account": acct.to_dict(),
        "stats": {"total": len(jobs), "awaiting_approval": awaiting, "published": published,
                  "quota_remaining": acct.quota_limit - acct.quota_used_today},
        "queue": [j.to_dict() for j in jobs[:100]],
    }
