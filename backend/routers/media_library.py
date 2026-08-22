"""Media Library — tudo que o sistema já produziu, num só lugar.

Lista os arquivos REAIS gerados (vídeo principal, shorts, thumbnails) a partir
dos jobs do banco, com tamanho em disco medido de verdade (stat), para a página
Mídia. Sem arquivo no disco = item marcado como ausente (não finge que existe).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import JobStatus, VideoJob

router = APIRouter(prefix="/media", tags=["media"])


def _file_info(path: str | None) -> dict | None:
    if not path:
        return None
    p = Path(path)
    exists = p.is_file()
    return {
        "path": path,
        "exists": exists,
        "size_mb": round(p.stat().st_size / 1e6, 1) if exists else None,
    }


@router.get("/library")
def library(
    kind: str = Query("all", pattern="^(all|video|short|thumb)$"),
    limit: int = Query(120, le=500),
    db: Session = Depends(get_db),
):
    jobs = db.execute(
        select(VideoJob)
        .where(VideoJob.status.in_([
            JobStatus.AWAITING_APPROVAL, JobStatus.APPROVED, JobStatus.PUBLISHING,
            JobStatus.PUBLISHED, JobStatus.ERROR, JobStatus.TIKTOK_PENDING_APPROVAL,
        ]))
        .order_by(VideoJob.updated_at.desc())
        .limit(limit)
    ).scalars().all()

    items: list[dict] = []
    total_mb = 0.0
    for job in jobs:
        status = job.status.value if hasattr(job.status, "value") else job.status
        base = {
            "job_id": job.id,
            "title": job.title,
            "status": status,
            "format": job.video_format,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }
        entries: list[tuple[str, str | None]] = []
        if kind in ("all", "video"):
            entries.append(("video", job.main_video_path))
        if kind in ("all", "short"):
            entries += [("short", p) for p in (job.shorts_paths or [])]
        if kind in ("all", "thumb"):
            entries.append(("thumb", job.thumbnail_path))
        for k, path in entries:
            info = _file_info(path)
            if info is None or not info["exists"]:
                continue  # não lista fantasma: arquivo que não existe não é mídia
            total_mb += info["size_mb"] or 0
            items.append({**base, "kind": k, **info})

    return {"items": items, "count": len(items), "total_mb": round(total_mb, 1)}
