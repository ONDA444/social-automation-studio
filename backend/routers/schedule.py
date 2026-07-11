"""Schedule API — per-account config + computed posting slots + calendar."""
from __future__ import annotations

import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agents.content_calendar import ContentCalendarAgent
from backend.database import get_db
from backend.models import JobStatus, PlatformAccount, ScheduleConfig, VideoJob

router = APIRouter(prefix="/schedule", tags=["schedule"])

_MANUAL_UPLOAD_EXTS = {".mp4", ".mov", ".webm", ".mkv"}


class ScheduleConfigIn(BaseModel):
    mode: str = "fixed"
    timezone: str = "America/Sao_Paulo"
    videos_per_day: int = Field(1, ge=1, le=6)
    post_times: list[str] = []
    auto_shorts: bool = True
    shorts_formats: list[int] = []

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in {"fixed", "smart", "trending_aware"}:
            raise ValueError("modo de publicacao invalido")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value or "America/Sao_Paulo")
        except Exception as exc:  # noqa: BLE001
            raise ValueError("timezone invalido") from exc
        return value

    @field_validator("post_times")
    @classmethod
    def validate_post_times(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for raw in values or []:
            value = str(raw or "").strip()
            if not value:
                continue
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
                raise ValueError("horario invalido; use HH:MM")
            if value not in out:
                out.append(value)
        return out[:6]


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
    cal = ContentCalendarAgent(db)
    slots = cal.next_slots(account_id, count, mode)
    cfg = db.execute(
        select(ScheduleConfig).where(ScheduleConfig.account_id == account_id)
    ).scalars().first()
    resolved_mode = mode or (cfg.mode if cfg else "fixed")
    per_day = (cfg.videos_per_day if cfg else None) or 1
    effective_times = cal.resolve_post_times(account_id, cfg, per_day, resolved_mode)
    has_analytics = resolved_mode == "smart" and bool(cal._smart_times(account_id, None))
    return {
        "account_id": account_id,
        "slots": [s.isoformat() for s in slots],
        "effective_times": effective_times,
        "from_analytics": has_analytics,
    }


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


@router.post("/{account_id}/upload-video")
async def upload_video(
    account_id: int,
    file: UploadFile = File(...),
    scheduled_at: str | None = Form(None),
    hint: str = Form(""),
    db: Session = Depends(get_db),
):
    """Manual "Enviar video do PC": analyze a user-picked local file with the
    same engine used for Drive ready-videos and land it in Approvals. A
    standalone one-off job — never added to the Drive rotation/inventory."""
    from backend.config import settings
    from backend.pipeline.dispatch import dispatch_analyze_upload

    account = db.get(PlatformAccount, account_id)
    if account is None:
        raise HTTPException(404, f"conta {account_id} não encontrada")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _MANUAL_UPLOAD_EXTS:
        raise HTTPException(
            400, f"Formato não suportado ({ext or 'sem extensão'}). "
                 f"Use: {', '.join(sorted(_MANUAL_UPLOAD_EXTS))}."
        )

    # DB convention (see publisher.py::run_publish) is NAIVE UTC. A tz-aware
    # value converts straight to UTC; a naive "datetime-local" value from the
    # browser is the user's own wall-clock time — interpreted in the channel's
    # schedule timezone (falls back to America/Sao_Paulo), then converted to UTC.
    from datetime import timezone as _tz
    parsed_scheduled_at = datetime.utcnow()
    if scheduled_at:
        try:
            raw = scheduled_at.replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is not None:
                parsed_scheduled_at = dt.astimezone(_tz.utc).replace(tzinfo=None)
            else:
                cfg = db.execute(
                    select(ScheduleConfig).where(ScheduleConfig.account_id == account_id)
                ).scalars().first()
                local_tz = ZoneInfo((cfg.timezone if cfg else None) or "America/Sao_Paulo")
                aware = dt.replace(tzinfo=local_tz)
                parsed_scheduled_at = aware.astimezone(_tz.utc).replace(tzinfo=None)
        except ValueError as exc:
            raise HTTPException(400, "scheduled_at inválido; use ISO 8601.") from exc

    hint = (hint or "").strip()[:200]

    job = VideoJob(
        title=(hint or "Vídeo enviado manualmente")[:300],
        topic=hint or None,
        mode="from_manual_upload",
        content_type="film_recap_ai_images",
        video_format="long",
        target_platforms=["youtube"],
        account_id=account.id,
        status=JobStatus.PROCESSING,
        approval_status="pending",
        scheduled_at=parsed_scheduled_at,
        current_agent="manual_upload",
        video_context={"source": "manual_upload", "hint": hint},
    )
    db.add(job)
    db.flush()

    dest_dir = settings.abs_path(settings.temp_dir) / "manual_uploads" / f"job_{job.id}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", file.filename or "video")[:180] or f"upload_{job.id}{ext}"
    dest = dest_dir / safe_name

    max_bytes = settings.max_manual_upload_mb * 1024 * 1024
    written = 0
    try:
        with dest.open("wb") as fh:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    fh.close()
                    dest.unlink(missing_ok=True)
                    db.delete(job)
                    db.commit()
                    raise HTTPException(
                        413, f"Arquivo maior que o limite de {settings.max_manual_upload_mb}MB."
                    )
                fh.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — disk/IO failure during streamed write
        dest.unlink(missing_ok=True)
        job.status = JobStatus.ERROR
        job.error_message = f"Falha ao salvar o vídeo enviado: {exc}"[:500]
        db.commit()
        raise HTTPException(500, "Falha ao salvar o vídeo enviado.") from exc

    if written == 0:
        dest.unlink(missing_ok=True)
        db.delete(job)
        db.commit()
        raise HTTPException(400, "Arquivo vazio.")

    job.main_video_path = str(dest)
    db.commit()

    dispatch_analyze_upload(job.id)
    return {"job_id": job.id, "status": "processing"}
