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


class ThemeCreate(BaseModel):
    account_id: int | None = None
    themes: list[str] = Field(default_factory=list)
    content_type: str = "film_recap_ai_images"
    target_platforms: list[str] = Field(default_factory=lambda: ["youtube"])


class BulkDelete(BaseModel):
    ids: list[int] | None = None
    status: str | None = None


@router.post("")
def create_themes(payload: ThemeCreate, db: Session = Depends(get_db)):
    """Create one ThemeQueue row per (trimmed, non-empty) theme, FIFO-ordered."""
    if payload.content_type not in CONTENT_TYPE_KEYS:
        raise HTTPException(400, f"content_type inválido: {payload.content_type}")

    if payload.account_id is not None:
        acct = db.get(PlatformAccount, payload.account_id)
        if acct is None:
            raise HTTPException(404, "conta não encontrada")

    cleaned = [t.strip() for t in payload.themes if t and t.strip()]
    cleaned = cleaned[:MAX_THEMES]
    if not cleaned:
        return {"created": 0, "ids": []}

    # Continue FIFO ordering after the current max position.
    base = db.query(ThemeQueue).count()

    rows: list[ThemeQueue] = []
    for offset, theme in enumerate(cleaned):
        rows.append(
            ThemeQueue(
                account_id=payload.account_id,
                theme=theme,
                content_type=payload.content_type,
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


@router.post("/bulk-delete")
def bulk_delete_themes(payload: BulkDelete, db: Session = Depends(get_db)):
    """Delete by explicit ids and/or by status; returns the deleted ids."""
    q = db.query(ThemeQueue)
    if payload.ids is None and payload.status is None:
        return {"deleted": []}
    if payload.ids is not None:
        q = q.filter(ThemeQueue.id.in_(payload.ids))
    if payload.status is not None:
        q = q.filter(ThemeQueue.status == payload.status)

    rows = q.all()
    deleted = [r.id for r in rows]
    for row in rows:
        db.delete(row)
    db.commit()
    return {"deleted": deleted}
