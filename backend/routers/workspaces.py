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
    accounts = svc.list()

    # Single grouped query for all accounts instead of one query per account (avoids N+1).
    account_ids = [acct.id for acct in accounts]
    counts_by_account: dict[int, dict] = {}
    if account_ids:
        rows = db.execute(
            select(VideoJob.account_id, VideoJob.status, func.count())
            .where(VideoJob.account_id.in_(account_ids))
            .group_by(VideoJob.account_id, VideoJob.status)
        ).all()
        for account_id, status, count in rows:
            counts_by_account.setdefault(account_id, {})[status] = count

    out = []
    for acct in accounts:
        counts = counts_by_account.get(acct.id, {})
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
