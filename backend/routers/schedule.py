"""Schedule API — per-account config + computed posting slots + calendar."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agents.content_calendar import ContentCalendarAgent
from backend.database import get_db
from backend.models import PlatformAccount, ScheduleConfig, VideoJob

router = APIRouter(prefix="/schedule", tags=["schedule"])


class ScheduleConfigIn(BaseModel):
    mode: str = "fixed"
    timezone: str = "America/Sao_Paulo"
    videos_per_day: int = 1
    post_times: list[str] = []
    auto_shorts: bool = True
    shorts_formats: list[int] = []


@router.get("/config/{account_id}")
def get_config(account_id: int, db: Session = Depends(get_db)):
    cfg = db.execute(
        select(ScheduleConfig).where(ScheduleConfig.account_id == account_id)
    ).scalars().first()
    return cfg.to_dict() if cfg else {"account_id": account_id, "mode": "fixed", "post_times": []}


@router.put("/config/{account_id}")
def upsert_config(account_id: int, payload: ScheduleConfigIn, db: Session = Depends(get_db)):
    # Validate the account exists first — otherwise we'd insert a ScheduleConfig
    # with a dangling FK (500 on Postgres / orphan row on SQLite).
    if db.get(PlatformAccount, account_id) is None:
        raise HTTPException(404, f"conta {account_id} não encontrada")
    cfg = db.execute(
        select(ScheduleConfig).where(ScheduleConfig.account_id == account_id)
    ).scalars().first()
    if not cfg:
        cfg = ScheduleConfig(account_id=account_id)
        db.add(cfg)
    for k, v in payload.model_dump().items():
        setattr(cfg, k, v)
    db.commit()
    db.refresh(cfg)
    return cfg.to_dict()


@router.get("/{account_id}/slots")
def next_slots(account_id: int, count: int = Query(5, le=30), mode: str | None = None,
               db: Session = Depends(get_db)):
    slots = ContentCalendarAgent(db).next_slots(account_id, count, mode)
    return {"account_id": account_id, "slots": [s.isoformat() for s in slots]}


@router.get("/calendar")
def calendar(db: Session = Depends(get_db)):
    rows = db.execute(
        select(VideoJob).where(VideoJob.scheduled_at.isnot(None)).order_by(VideoJob.scheduled_at)
    ).scalars().all()
    return {"events": [{"job_id": j.id, "title": j.title, "platforms": j.target_platforms,
                        "scheduled_at": j.scheduled_at.isoformat() if j.scheduled_at else None,
                        "status": j.status.value if hasattr(j.status, "value") else j.status,
                        "account_id": j.account_id,
                        "channel_name": j.account.display_name if j.account else "Sem canal"}
                       for j in rows]}
