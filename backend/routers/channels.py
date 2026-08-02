"""Channels API -- Channel CRUD, daily agenda, and manual refresh.

Channel wraps a PlatformAccount 1:1 (see backend/models/channel.py); this
router is additive on top of the existing /accounts and /schedule routers,
not a replacement for either.
"""
from __future__ import annotations

import logging
import threading
from datetime import date as date_, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database import SessionLocal, get_db
from backend.models import Channel, PlatformAccount, PublishSession, VideoJob

router = APIRouter(prefix="/channels", tags=["channels"])
logger = logging.getLogger("studio.channels")


class ChannelCreate(BaseModel):
    account_id: int
    name: str = Field(..., min_length=1)
    youtube_channel_id: str | None = None
    niche: str | None = None
    tts_voice: str = "pt-BR-AntonioNeural"
    visual_theme: dict = Field(default_factory=dict)
    daily_limit_long: int = Field(1, ge=0, le=20)
    daily_limit_short: int = Field(3, ge=0, le=50)
    posting_window_start: str = "08:00"
    posting_window_end: str = "23:00"
    active: bool = True


class ChannelPatch(BaseModel):
    name: str | None = None
    niche: str | None = None
    tts_voice: str | None = None
    visual_theme: dict | None = None
    daily_limit_long: int | None = Field(None, ge=0, le=20)
    daily_limit_short: int | None = Field(None, ge=0, le=50)
    posting_window_start: str | None = None
    posting_window_end: str | None = None
    active: bool | None = None


def _get_channel(db: Session, channel_id: int) -> Channel:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(404, f"canal {channel_id} não encontrado")
    return channel


@router.get("")
def list_channels(db: Session = Depends(get_db)):
    channels = db.execute(select(Channel).order_by(Channel.id)).scalars().all()
    return {"channels": [c.to_dict() for c in channels]}


@router.post("")
def create_channel(payload: ChannelCreate, db: Session = Depends(get_db)):
    if db.get(PlatformAccount, payload.account_id) is None:
        raise HTTPException(404, f"conta {payload.account_id} não encontrada")
    existing = db.execute(
        select(Channel).where(Channel.account_id == payload.account_id)
    ).scalars().first()
    if existing is not None:
        raise HTTPException(409, f"conta {payload.account_id} já tem um canal (id={existing.id})")
    channel = Channel(**payload.model_dump())
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return channel.to_dict()


@router.get("/{channel_id}")
def get_channel(channel_id: int, db: Session = Depends(get_db)):
    return _get_channel(db, channel_id).to_dict()


@router.patch("/{channel_id}")
def patch_channel(channel_id: int, payload: ChannelPatch, db: Session = Depends(get_db)):
    channel = _get_channel(db, channel_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(channel, field, value)
    db.commit()
    db.refresh(channel)
    return channel.to_dict()


def _parse_date(value: str | None) -> date_:
    if not value:
        return datetime.utcnow().date()
    try:
        return date_.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(400, "date inválida; use YYYY-MM-DD.") from exc


@router.get("/{channel_id}/agenda")
def get_agenda(channel_id: int, date: str | None = Query(None), db: Session = Depends(get_db)):
    """Read-only: the channel's PublishSession for `date` (default: today) plus
    a summary of each planned job. Does NOT generate anything -- see the
    /agenda/generate POST for that (it does real, possibly slow, work)."""
    from backend.agents.agenda import get_or_create_session

    channel = _get_channel(db, channel_id)
    target_date = _parse_date(date)
    session = get_or_create_session(db, channel, target_date)
    db.commit()

    jobs = []
    if session.planned_items:
        rows = db.execute(
            select(VideoJob).where(VideoJob.id.in_(session.planned_items))
        ).scalars().all()
        by_id = {j.id: j for j in rows}
        jobs = [
            {
                "job_id": jid,
                "title": by_id[jid].title if jid in by_id else None,
                "video_format": by_id[jid].video_format if jid in by_id else None,
                "scheduled_at": by_id[jid].scheduled_at.isoformat()
                if jid in by_id and by_id[jid].scheduled_at else None,
                "status": by_id[jid].status.value if jid in by_id else "missing",
            }
            for jid in session.planned_items
        ]
    return {"session": session.to_dict(), "jobs": jobs}


def _run_generate(channel_id: int, target_date: date_) -> None:
    from backend.agents.agenda import generate_daily_agenda

    db = SessionLocal()
    try:
        channel = db.get(Channel, channel_id)
        if channel is None:
            return
        generate_daily_agenda(db, channel, target_date)
    except Exception as exc:  # noqa: BLE001
        logger.warning("agenda generation failed for channel %s (%s): %s", channel_id, target_date, exc)
    finally:
        db.close()


@router.post("/{channel_id}/agenda/generate")
def generate_agenda(channel_id: int, date: str | None = Query(None), db: Session = Depends(get_db)):
    """Kicks off agenda generation on a background thread and returns
    immediately -- reserving a ready video + running curation is the same
    synchronous work the automatic scheduler does per job, too slow to hold
    an HTTP request open for (see agents/agenda.py's docstring)."""
    channel = _get_channel(db, channel_id)
    target_date = _parse_date(date)
    threading.Thread(
        target=_run_generate, args=(channel.id, target_date), daemon=True,
        name=f"agenda-gen-ch{channel.id}",
    ).start()
    return {"status": "started", "channel_id": channel.id, "date": target_date.isoformat()}


class PreviewRequest(BaseModel):
    video_format: str = "short"
    line: str = Field(..., min_length=1)
    job_id: int | None = None  # optional: reuse a real job's source frame/analysis


@router.post("/{channel_id}/preview")
def preview_design(channel_id: int, payload: PreviewRequest, db: Session = Depends(get_db)):
    """Design-only preview: renders just the overlay/intro-card PNG a real
    curation pass would produce for this channel's visual_theme, without any
    video encode -- fast iteration on colors/text before a real render."""
    from backend.agents.ready_video_curation import render_preview

    channel = _get_channel(db, channel_id)
    source_path = None
    analysis = None
    preview_job_id = payload.job_id or channel_id
    if payload.job_id is not None:
        job = db.get(VideoJob, payload.job_id)
        if job is None or job.account_id != channel.account_id:
            raise HTTPException(404, f"job {payload.job_id} não encontrado neste canal")
        source_path = job.main_video_path
        analysis = (job.video_context or {}).get("content_analysis")

    out_path = render_preview(
        preview_job_id, payload.video_format, payload.line,
        visual_theme=channel.resolved_visual_theme(),
        source_path=source_path, analysis=analysis,
    )
    if not out_path:
        raise HTTPException(500, "falha ao gerar preview")
    return {"preview_path": out_path, "media_url": f"/media?path={out_path}"}


@router.post("/{channel_id}/refresh")
def refresh_channel(channel_id: int, job_id: int | None = Query(None), db: Session = Depends(get_db)):
    """Best-effort re-curation of one job (or every eligible job in the
    channel's most recent session when job_id is omitted). See
    agents/channel_refresh.py for the change-detection/idempotency logic."""
    from backend.agents.channel_refresh import refresh_channel as _refresh_channel, refresh_job

    channel = _get_channel(db, channel_id)
    if job_id is not None:
        job = db.get(VideoJob, job_id)
        if job is None or job.account_id != channel.account_id:
            raise HTTPException(404, f"job {job_id} não encontrado neste canal")
        result = refresh_job(db, channel, job)
        return {"results": [result]}
    return {"results": _refresh_channel(db, channel)}
