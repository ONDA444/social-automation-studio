"""Theme queue API — FIFO list of themes/titles consumed by the pipeline."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.content_types import CONTENT_TYPE_KEYS
from backend.database import get_db
from backend.models.platform_account import PlatformAccount
from backend.models.theme_queue import ThemeQueue

router = APIRouter(prefix="/themes", tags=["themes"])

MAX_THEMES = 300
THEME_STATUSES = {"pending", "consumed"}


class ThemeCreate(BaseModel):
    account_id: int | None = None
    themes: list[str] = Field(default_factory=list)
    content_type: str = "film_recap_ai_images"
    format: str = "long"  # long (16:9) | short (9:16 vertical nativo)
    target_platforms: list[str] = Field(default_factory=lambda: ["youtube"])


class BulkDelete(BaseModel):
    ids: list[int] | None = None
    status: str | None = None
    account_id: int | None = None


@router.post("")
def create_themes(payload: ThemeCreate, db: Session = Depends(get_db)):
    """Create one ThemeQueue row per (trimmed, non-empty) theme, FIFO-ordered."""
    if payload.content_type not in CONTENT_TYPE_KEYS:
        raise HTTPException(400, f"content_type inválido: {payload.content_type}")
    if payload.format not in {"long", "short"}:
        raise HTTPException(400, f"format inválido: {payload.format}")

    if payload.account_id is not None:
        acct = db.get(PlatformAccount, payload.account_id)
        if acct is None:
            raise HTTPException(404, "conta não encontrada")

    cleaned = [t.strip() for t in payload.themes if t and t.strip()]
    cleaned = cleaned[:MAX_THEMES]
    if not cleaned:
        return {"created": 0, "ids": []}

    # Continue FIFO ordering after the current max position. with_for_update()
    # takes a row lock on the actual max-position row (Postgres) so a
    # concurrent POST /themes blocks until this transaction commits, instead
    # of both requests reading the same base and producing colliding
    # positions. NOTE: can't lock an aggregate directly — Postgres rejects
    # "SELECT max(x) ... FOR UPDATE" with FeatureNotSupported (confirmed in
    # production: this 500'd on every single call, i.e. the whole "Adicionar
    # a fila" button was broken) — lock the real row instead and read its
    # column.
    max_row = (
        db.query(ThemeQueue)
        .order_by(ThemeQueue.position.desc())
        .with_for_update()
        .first()
    )
    base = 0 if max_row is None else max_row.position + 1

    rows: list[ThemeQueue] = []
    for offset, theme in enumerate(cleaned):
        rows.append(
            ThemeQueue(
                account_id=payload.account_id,
                theme=theme,
                content_type=payload.content_type,
                video_format=payload.format,
                target_platforms=payload.target_platforms or ["youtube"],
                status="pending",
                position=base + offset,
            )
        )
    db.add_all(rows)
    db.commit()
    for row in rows:
        db.refresh(row)

    return {"created": len(rows), "ids": [r.id for r in rows]}


@router.get("")
def list_themes(
    account_id: int | None = Query(None),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(ThemeQueue)
    if account_id is not None:
        q = q.filter(ThemeQueue.account_id == account_id)
    if status is not None:
        q = q.filter(ThemeQueue.status == status)
    q = q.order_by(ThemeQueue.position.asc(), ThemeQueue.id.asc())
    return {"themes": [t.to_dict() for t in q.all()]}


@router.delete("/{theme_id}")
def delete_theme(theme_id: int, db: Session = Depends(get_db)):
    row = db.get(ThemeQueue, theme_id)
    if row is None:
        raise HTTPException(404, "tema não encontrado")
    db.delete(row)
    db.commit()
    return {"deleted": theme_id}


@router.post("/restore")
def restore_consumed(account_id: int | None = Query(None), db: Session = Depends(get_db)):
    """
    Undo premature generation: themes marked 'consumed' whose video has NOT gone
    out yet are returned to 'pending' (and their not-yet-published job deleted), so
    the slot-gated scheduler regenerates them AT their proper publish time.

    Jobs already PUBLISHED/PUBLISHING are kept; a job still PROCESSING is left to
    finish (its theme stays consumed) to avoid yanking an in-flight render.
    """
    from backend.models import JobStatus, VideoJob

    cancellable = {
        JobStatus.QUEUED,
        JobStatus.ERROR,
        JobStatus.AWAITING_APPROVAL,
        JobStatus.APPROVED,
        JobStatus.REJECTED,
    }
    q = db.query(ThemeQueue).filter(ThemeQueue.status == "consumed")
    if account_id is not None:
        q = q.filter(ThemeQueue.account_id == account_id)

    restored: list[int] = []
    deleted_jobs: list[int] = []
    for th in q.all():
        job = db.get(VideoJob, th.consumed_job_id) if th.consumed_job_id else None
        if job is not None and job.status not in cancellable:
            continue  # processing/publishing/published -> leave it
        th.status = "pending"
        th.consumed_job_id = None
        restored.append(th.id)
        if job is not None:
            deleted_jobs.append(job.id)
            db.delete(job)
    db.commit()
    return {"restored_themes": restored, "deleted_jobs": deleted_jobs}


@router.post("/bulk-delete")
def bulk_delete_themes(payload: BulkDelete, db: Session = Depends(get_db)):
    """Delete by explicit ids and/or by status, optionally scoped to one account.

    A status-only delete MUST be scoped to an account_id: without it, a single
    "limpar fila" click would wipe that status across EVERY channel. We refuse the
    unscoped case so the queue of one channel can never silently erase another's.
    """
    if payload.ids is None and payload.status is None:
        return {"deleted": []}
    if payload.status is not None and payload.status not in THEME_STATUSES:
        raise HTTPException(400, f"status inválido: {payload.status}")
    if payload.ids is None and payload.account_id is None:
        raise HTTPException(
            400, "account_id obrigatório ao limpar por status (evita apagar de todos os canais)"
        )

    q = db.query(ThemeQueue)
    if payload.ids is not None:
        q = q.filter(ThemeQueue.id.in_(payload.ids))
    if payload.status is not None:
        q = q.filter(ThemeQueue.status == payload.status)
    if payload.account_id is not None:
        q = q.filter(ThemeQueue.account_id == payload.account_id)

    rows = q.all()
    deleted = [r.id for r in rows]
    for row in rows:
        db.delete(row)
    db.commit()
    return {"deleted": deleted}
