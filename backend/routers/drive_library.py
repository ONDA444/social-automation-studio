"""Google Drive ready-video library API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.agents.drive_library import DriveLibraryService, extract_folder_id
from backend.config import settings
from backend.database import get_db
from backend.models import PlatformAccount, ReadyVideo

router = APIRouter(prefix="/drive-library", tags=["drive-library"])


class ImportRequest(BaseModel):
    folder: str
    niche: str | None = None
    content_type: str = "auto"
    format: str | None = None
    account_id: int | None = None
    recursive: bool = True


@router.get("/status")
def status(db: Session = Depends(get_db)):
    stats = dict(
        db.execute(select(ReadyVideo.status, func.count(ReadyVideo.id)).group_by(ReadyVideo.status)).all()
    )
    return {
        **DriveLibraryService(db).status(),
        "redirect_uri": settings.google_drive_redirect_uri,
        "stats": stats,
    }


@router.get("/auth/start")
def auth_start(db: Session = Depends(get_db)):
    try:
        result = DriveLibraryService(db).build_auth_url()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Falha ao iniciar Google Drive: {exc}") from exc
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Drive OAuth indisponivel."))
    return {"auth_url": result["auth_url"], "redirect_uri": settings.google_drive_redirect_uri}


@router.get("/auth/callback", response_class=HTMLResponse)
def auth_callback(code: str = Query(""), error: str = Query(""), db: Session = Depends(get_db)):
    if error:
        return _html(f"Autorizacao cancelada: {error}")
    if not code:
        return _html("Callback invalido.")
    try:
        DriveLibraryService(db).exchange_code(code)
    except Exception as exc:  # noqa: BLE001
        return _html(f"Falha ao conectar Drive: {exc}")
    return _html("Drive conectado com sucesso. Pode fechar esta janela.")


@router.post("/disconnect")
def disconnect(db: Session = Depends(get_db)):
    return DriveLibraryService(db).disconnect()


@router.post("/import")
def import_folder(payload: ImportRequest, db: Session = Depends(get_db)):
    try:
        result = DriveLibraryService(db).index_folder(
            payload.folder,
            niche=payload.niche,
            content_type=payload.content_type,
            video_format=payload.format,
            account_id=payload.account_id,
            recursive=payload.recursive,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc))
    return result


@router.post("/accounts/{account_id}/sync")
def sync_account_folder(account_id: int, db: Session = Depends(get_db)):
    acct = db.get(PlatformAccount, account_id)
    if not acct:
        raise HTTPException(404, "conta nao encontrada")
    folder = acct.drive_folder_id or extract_folder_id(acct.drive_folder_url)
    if not folder:
        raise HTTPException(400, "Configure a pasta do Drive neste canal.")
    drive_status = DriveLibraryService(db).status()
    if not drive_status.get("has_credentials") and not drive_status.get("api_key_configured"):
        raise HTTPException(400, "Conecte o Google Drive antes de sincronizar a pasta.")
    try:
        return DriveLibraryService(db).index_folder(
            folder,
            niche=acct.drive_niche or acct.niche,
            content_type="auto",
            account_id=acct.id,
            recursive=bool(getattr(acct, "drive_recursive", True)),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc))


@router.get("/videos")
def list_videos(
    account_id: int | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    niche: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    stmt = select(ReadyVideo).order_by(ReadyVideo.created_at.desc()).limit(limit)
    if account_id is not None:
        stmt = stmt.where((ReadyVideo.account_id == account_id) | (ReadyVideo.account_id.is_(None)))
    if status_filter:
        stmt = stmt.where(ReadyVideo.status == status_filter)
    if niche:
        stmt = stmt.where(ReadyVideo.niche.ilike(f"%{niche.strip()}%"))
    rows = db.execute(stmt).scalars().all()
    return {"videos": [r.to_dict() for r in rows]}


def _html(msg: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8"><title>Google Drive</title></head>
        <body style="font-family:system-ui;background:#0A0A0F;color:#F0F0FF;display:flex;
        align-items:center;justify-content:center;height:100vh;margin:0">
        <div style="text-align:center"><h2>{msg}</h2>
        <script>setTimeout(()=>window.close(),2500)</script></div></body></html>"""
    )
