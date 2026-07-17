"""
ErrorRecoveryAgent — system health heartbeat + automatic recovery.

Heartbeat (every 30s via scheduler) checks Redis, DB, FFmpeg, disk, and LLM
reachability, broadcasting a per-service traffic-light to the dashboard. On
DISK_LOW it prunes old cache and archives published outputs. Quota/auth recovery
is handled where the error originates (publisher/account service); this module
provides the shared helpers + the status snapshot.
"""
from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from backend.config import settings
from backend.events import publish_event

logger = logging.getLogger("studio.recovery")

DISK_MIN_FREE = 500 * 1024 * 1024  # 500 MB
CACHE_MAX_AGE_DAYS = 7


def get_system_health() -> dict:
    checks: dict[str, dict] = {}

    # DB
    try:
        from sqlalchemy import text

        from backend.database import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = {"status": "green", "detail": "ok"}
    except Exception as exc:  # noqa: BLE001
        checks["database"] = {"status": "red", "detail": str(exc)[:80]}

    # Redis
    try:
        import redis

        redis.from_url(settings.redis_url, socket_connect_timeout=1).ping()
        checks["redis"] = {"status": "green", "detail": "ok"}
    except Exception:
        checks["redis"] = {"status": "yellow", "detail": "indisponível (modo in-process)"}

    # FFmpeg
    checks["ffmpeg"] = ({"status": "green", "detail": "ok"} if shutil.which("ffmpeg")
                        else {"status": "red", "detail": "ausente no PATH"})

    # Disk
    free = _free_bytes()
    checks["disk"] = {
        "status": "green" if free > DISK_MIN_FREE else "yellow",
        "detail": f"{free / 1e9:.1f} GB livres",
    }

    # LLM keys
    checks["llm"] = ({"status": "green", "detail": "chave configurada"}
                     if (settings.groq_api_key or settings.gemini_api_key)
                     else {"status": "yellow", "detail": "sem chave LLM (usando offline)"})

    # Scheduler (fila, publicação, retry automático, etc. rodam daqui). A
    # non-leader replica never runs it by design — that's not a failure.
    try:
        from backend.scheduler import scheduler_status

        sched = scheduler_status()
        if not sched["leader"]:
            checks["scheduler"] = {"status": "green", "detail": "réplica secundária (roda em outra instância)"}
        elif sched["alive"]:
            checks["scheduler"] = {"status": "green", "detail": "ativo"}
        else:
            checks["scheduler"] = {
                "status": "red",
                "detail": "parado ou travado — tarefas automáticas (fila, publicação, retry) não estão rodando",
            }
    except Exception as exc:  # noqa: BLE001
        checks["scheduler"] = {"status": "red", "detail": str(exc)[:80]}

    overall = "green"
    if any(c["status"] == "red" for c in checks.values()):
        overall = "red"
    elif any(c["status"] == "yellow" for c in checks.values()):
        overall = "yellow"
    return {"status": overall, "checks": checks, "ts": datetime.utcnow().isoformat()}


def heartbeat() -> dict:
    health = get_system_health()
    publish_event({"type": "system_health", **health})
    if health["checks"].get("disk", {}).get("status") != "green":
        freed = handle_disk_low()
        logger.warning("DISK_LOW — freed %.1f MB", freed / 1e6)
    return health


def _free_bytes() -> int:
    try:
        return shutil.disk_usage(str(settings.abs_path(settings.output_dir))).free
    except Exception:
        return DISK_MIN_FREE * 2


def handle_disk_low() -> int:
    """Prune cache older than 7 days and archive published outputs. Returns bytes freed."""
    freed = cleanup_old_cache(CACHE_MAX_AGE_DAYS)
    freed += archive_published_outputs()
    return freed


def cleanup_old_cache(days: int = CACHE_MAX_AGE_DAYS) -> int:
    cutoff = time.time() - days * 86400
    freed = 0
    cache = settings.abs_path(settings.cache_dir)
    for sub in ("broll", "remix_refs", "thumbnails"):
        d = cache / sub
        if not d.exists():
            continue
        for f in d.iterdir():
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    freed += f.stat().st_size
                    f.unlink()
            except Exception:
                continue
    return freed


def archive_published_outputs() -> int:
    """Move outputs of published jobs into output/archive/ (keeps working dir lean)."""
    from sqlalchemy import select

    from backend.database import SessionLocal
    from backend.models import JobStatus, VideoJob

    moved = 0
    out_dir = settings.abs_path(settings.output_dir)
    archive = out_dir / "archive"
    archive.mkdir(exist_ok=True)
    cutoff = datetime.utcnow() - timedelta(days=2)
    db = SessionLocal()
    try:
        jobs = db.execute(
            select(VideoJob).where(VideoJob.status == JobStatus.PUBLISHED,
                                   VideoJob.updated_at < cutoff)
        ).scalars().all()
        for job in jobs:
            src = out_dir / f"job_{job.id}"
            if src.exists() and src.is_dir():
                dst = archive / f"job_{job.id}"
                try:
                    if not dst.exists():
                        size = sum(f.stat().st_size for f in src.rglob("*") if f.is_file())
                        shutil.move(str(src), str(dst))
                        moved += size
                except Exception:
                    continue
    finally:
        db.close()
    return moved


if __name__ == "__main__":
    import json

    print(json.dumps(get_system_health(), indent=2, ensure_ascii=False))
