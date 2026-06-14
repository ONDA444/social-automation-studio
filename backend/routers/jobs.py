"""Jobs API — create/list/detail/delete, approve/reject, edit SEO, CSV import."""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import JobStatus, PlatformAccount, VideoJob
from backend.pipeline.dispatch import dispatch_job, dispatch_publish

logger = logging.getLogger("studio")

router = APIRouter(prefix="/jobs", tags=["jobs"])

CONTENT_TYPES = {"film_recap_ai_images", "sports_highlights", "quote_viral"}
MODES = {"from_title", "from_remix", "from_idea"}


class JobCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    topic: str | None = None
    mode: str = "from_title"
    content_type: str = "film_recap_ai_images"
    account_id: int | None = None
    reference_url: str | None = None
    target_platforms: list[str] = Field(default_factory=lambda: ["youtube"])
    scheduled_at: datetime | None = None


class JobBatchCreate(BaseModel):
    themes: list[str] = Field(default_factory=list)
    content_type: str = "film_recap_ai_images"
    target_platforms: list[str] = Field(default_factory=lambda: ["youtube"])
    account_id: int | None = None


class SEOUpdate(BaseModel):
    seo_metadata: dict


class JobPatch(BaseModel):
    account_id: int | None = None
    target_platforms: list[str] | None = None
    scheduled_at: datetime | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)


class BulkDelete(BaseModel):
    ids: list[int] | None = None
    status: str | None = None


# Per-platform publish states that count as "already done" — never re-sent.
_PUBLISHED_STATES = {"ok", "published"}

# Statuses for which a job may still be edited (channel/platforms/schedule/title).
_EDITABLE_STATES = {
    JobStatus.QUEUED,
    JobStatus.AWAITING_APPROVAL,
    JobStatus.APPROVED,
    JobStatus.ERROR,
}

# Statuses that block destructive / re-dispatch actions (job is running).
_RUNNING_STATES = {JobStatus.PROCESSING, JobStatus.PUBLISHING}


def _validate(payload: JobCreate) -> None:
    if payload.content_type not in CONTENT_TYPES:
        raise HTTPException(400, f"content_type inválido: {payload.content_type}")
    if payload.mode not in MODES:
        raise HTTPException(400, f"mode inválido: {payload.mode}")


def _require_account(db: Session, account_id: int | None) -> None:
    """Raise 400 if account_id is given but not present in platform_accounts."""
    if account_id is None:
        return
    if db.get(PlatformAccount, account_id) is None:
        raise HTTPException(400, f"account_id inválido: {account_id} não existe")


def _cleanup_job_files(job: VideoJob) -> None:
    """Best-effort removal of a job's generated artifacts (video/thumb/shorts)."""
    paths: list[str] = []
    if job.main_video_path:
        paths.append(job.main_video_path)
    if job.thumbnail_path:
        paths.append(job.thumbnail_path)
    for sp in (job.shorts_paths or []):
        if sp:
            paths.append(sp)

    for p in paths:
        try:
            Path(p).unlink()
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha ao apagar arquivo %s do job %s: %s", p, job.id, exc)


@router.post("")
def create_job(payload: JobCreate, db: Session = Depends(get_db)):
    _validate(payload)
    _require_account(db, payload.account_id)
    job = VideoJob(
        title=payload.title,
        topic=payload.topic,
        mode=payload.mode,
        content_type=payload.content_type,
        account_id=payload.account_id,
        reference_url=payload.reference_url,
        target_platforms=payload.target_platforms,
        scheduled_at=payload.scheduled_at,
        status=JobStatus.QUEUED,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    transport = dispatch_job(job.id)
    return {"job": job.to_dict(), "dispatch": transport}


@router.post("/batch")
def create_jobs_batch(payload: JobBatchCreate, db: Session = Depends(get_db)):
    """Bulk-create one job per theme (max 200). Each is dispatched in-process;
    the in-process semaphore serializes them automatically."""
    if payload.content_type not in CONTENT_TYPES:
        raise HTTPException(400, f"content_type inválido: {payload.content_type}")
    _require_account(db, payload.account_id)

    themes = [t.strip() for t in payload.themes if t and t.strip()][:200]

    job_ids: list[int] = []
    for theme in themes:
        job = VideoJob(
            title=theme,
            topic=theme,
            content_type=payload.content_type,
            account_id=payload.account_id,
            target_platforms=payload.target_platforms,
            status=JobStatus.QUEUED,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        dispatch_job(job.id)
        job_ids.append(job.id)

    return {"created": len(job_ids), "job_ids": job_ids}


@router.get("")
def list_jobs(
    status: str | None = Query(None),
    account_id: int | None = Query(None),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
):
    stmt = select(VideoJob).order_by(VideoJob.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(VideoJob.status == status)
    if account_id is not None:
        stmt = stmt.where(VideoJob.account_id == account_id)
    jobs = db.execute(stmt).scalars().all()
    return {"jobs": [j.to_dict() for j in jobs], "count": len(jobs)}


@router.get("/approvals")
def list_approvals(db: Session = Depends(get_db)):
    """Jobs waiting for human review."""
    stmt = select(VideoJob).where(VideoJob.status == JobStatus.AWAITING_APPROVAL).order_by(
        VideoJob.updated_at.desc()
    )
    jobs = db.execute(stmt).scalars().all()
    return {"jobs": [j.to_dict() for j in jobs], "count": len(jobs)}


@router.get("/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job não encontrado")
    return job.to_dict()


@router.delete("/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job não encontrado")
    if job.status in _RUNNING_STATES:
        raise HTTPException(409, f"job em execução não pode ser deletado (status={job.status.value})")

    # Best-effort cleanup of generated artifacts before dropping the row.
    _cleanup_job_files(job)

    db.delete(job)
    db.commit()
    return {"deleted": job_id}


@router.post("/{job_id}/retry")
def retry_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job não encontrado")
    if job.status in _RUNNING_STATES:
        raise HTTPException(409, f"job em execução não pode ser reprocessado (status={job.status.value})")
    job.status = JobStatus.QUEUED
    job.error_message = None
    job.progress = 0
    job.retry_count = (job.retry_count or 0) + 1
    db.commit()
    transport = dispatch_job(job.id)
    return {"job": job.to_dict(), "dispatch": transport}


@router.patch("/{job_id}/seo")
def edit_seo(job_id: int, payload: SEOUpdate, db: Session = Depends(get_db)):
    job = db.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job não encontrado")
    job.seo_metadata = payload.seo_metadata
    db.commit()
    return {"job_id": job_id, "seo_metadata": job.seo_metadata}


@router.post("/{job_id}/approve")
def approve_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job não encontrado")
    if job.status != JobStatus.AWAITING_APPROVAL:
        raise HTTPException(409, f"job não está aguardando aprovação (status={job.status.value})")
    job.approval_status = "approved"
    job.status = JobStatus.APPROVED
    db.commit()

    dispatched = None
    try:
        import importlib.util

        if importlib.util.find_spec("backend.agents.publisher"):
            dispatched = dispatch_publish(job.id)
    except Exception:
        dispatched = None
    return {"job": job.to_dict(), "publish_dispatch": dispatched,
            "note": None if dispatched else "Publisher ainda não configurado — vídeo aprovado e salvo localmente."}


@router.post("/{job_id}/reject")
def reject_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job não encontrado")
    if job.status not in (JobStatus.AWAITING_APPROVAL, JobStatus.TIKTOK_PENDING_APPROVAL):
        raise HTTPException(409, f"job não está aguardando aprovação (status={job.status.value})")
    job.approval_status = "rejected"
    job.status = JobStatus.REJECTED
    db.commit()
    return {"job": job.to_dict()}


@router.post("/import-csv")
async def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Bulk-create jobs from a CSV with headers:
    title, topic, content_type, mode, target_platforms (pipe-separated), account_id
    """
    raw = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    created = []
    for row in reader:
        title = (row.get("title") or "").strip()
        if not title:
            continue
        ct = (row.get("content_type") or "film_recap_ai_images").strip()
        mode = (row.get("mode") or "from_title").strip()
        platforms = [p.strip() for p in (row.get("target_platforms") or "youtube").split("|") if p.strip()]
        acct = row.get("account_id")
        account_id = int(acct) if acct and acct.strip().isdigit() else None
        _require_account(db, account_id)
        job = VideoJob(
            title=title,
            topic=(row.get("topic") or None),
            content_type=ct if ct in CONTENT_TYPES else "film_recap_ai_images",
            mode=mode if mode in MODES else "from_title",
            target_platforms=platforms,
            account_id=account_id,
            status=JobStatus.QUEUED,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        dispatch_job(job.id)
        created.append(job.id)
    return {"created": created, "count": len(created)}


@router.post("/{job_id}/publish")
def publish_job(job_id: int, db: Session = Depends(get_db)):
    """Republish an already-produced job WITHOUT regenerating the video.

    Accepts jobs in APPROVED, ERROR or PUBLISHING (anything else -> 409).
    Promotes the job to APPROVED if needed, then dispatches the publisher.
    Idempotent: platforms already marked 'ok'/'published' in publish_status are
    not re-sent. This rescues orphaned APPROVED jobs recovered at boot that
    otherwise had no way to be published.
    """
    job = db.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job não encontrado")
    if job.status not in (JobStatus.APPROVED, JobStatus.ERROR, JobStatus.PUBLISHING):
        raise HTTPException(409, f"job não pode ser republicado (status={job.status.value})")

    pub = job.publish_status or {}
    targets = job.target_platforms or []
    pending = [p for p in targets if str(pub.get(p)) not in _PUBLISHED_STATES]

    # Everything already published — nothing to do (idempotent no-op).
    if targets and not pending:
        if job.status != JobStatus.PUBLISHED:
            job.status = JobStatus.PUBLISHED
            db.commit()
        return {"job": job.to_dict(), "publish_dispatch": None, "pending_platforms": [],
                "note": "Todas as plataformas já publicadas — nada a reenviar."}

    if job.status != JobStatus.APPROVED:
        job.status = JobStatus.APPROVED
    job.approval_status = "approved"
    db.commit()

    transport = dispatch_publish(job.id)
    return {"job": job.to_dict(), "publish_dispatch": transport, "pending_platforms": pending}


@router.patch("/{job_id}")
def patch_job(job_id: int, payload: JobPatch, db: Session = Depends(get_db)):
    """Edit a queued/pending job's channel, target platforms, schedule or title.

    Only allowed while status in (QUEUED, AWAITING_APPROVAL, APPROVED, ERROR);
    rejected with 409 for PROCESSING/PUBLISHING. account_id (when sent non-null)
    must exist in platform_accounts.
    """
    job = db.get(VideoJob, job_id)
    if not job:
        raise HTTPException(404, "job não encontrado")
    if job.status not in _EDITABLE_STATES:
        raise HTTPException(409, f"job não pode ser editado (status={job.status.value})")

    fields = payload.model_fields_set

    if "account_id" in fields:
        _require_account(db, payload.account_id)
        job.account_id = payload.account_id
    if "target_platforms" in fields and payload.target_platforms is not None:
        job.target_platforms = payload.target_platforms
    if "scheduled_at" in fields:
        job.scheduled_at = payload.scheduled_at
    if "title" in fields and payload.title is not None:
        job.title = payload.title

    db.commit()
    db.refresh(job)
    return {"job": job.to_dict()}


@router.post("/bulk-delete")
def bulk_delete_jobs(payload: BulkDelete, db: Session = Depends(get_db)):
    """Delete many jobs at once — by explicit ids or by status (e.g. clear all
    'error'). Reuses the per-job file cleanup of delete_job. Running jobs
    (PROCESSING/PUBLISHING) are skipped to avoid yanking work in progress."""
    if payload.ids:
        stmt = select(VideoJob).where(VideoJob.id.in_(payload.ids))
    elif payload.status:
        stmt = select(VideoJob).where(VideoJob.status == payload.status)
    else:
        raise HTTPException(400, "informe 'ids' ou 'status'")

    jobs = db.execute(stmt).scalars().all()
    deleted: list[int] = []
    for job in jobs:
        if job.status in _RUNNING_STATES:
            continue
        _cleanup_job_files(job)
        deleted.append(job.id)
        db.delete(job)
    db.commit()
    return {"deleted": deleted}
