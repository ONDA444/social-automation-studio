"""
SQLAlchemy 2.x engine + session + declarative base.

Usage:
    python -m backend.database --init      # create all tables
    python -m backend.database --reset     # drop + recreate (DEV ONLY)
"""
from __future__ import annotations

import argparse
import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import settings

logger = logging.getLogger("studio.db")

# SQLite needs check_same_thread=False for FastAPI's threadpool; Postgres ignores it.
_connect_args = {"check_same_thread": False} if settings.sqlalchemy_url.startswith("sqlite") else {}

engine = create_engine(
    settings.sqlalchemy_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency — yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Import all models so they register on Base.metadata, then create tables."""
    settings.ensure_dirs()
    import backend.models  # noqa: F401  (registers all mappers)

    Base.metadata.create_all(bind=engine)
    print(f"[OK] Database ready at: {settings.sqlalchemy_url}")


def _existing_columns(conn, table: str) -> set[str]:
    if settings.sqlalchemy_url.startswith("sqlite"):
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        return {r[1] for r in rows}
    rows = conn.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
        {"t": table},
    ).fetchall()
    return {r[0] for r in rows}


def ensure_columns() -> None:
    """Idempotent lightweight migration: add columns introduced after a table was
    first created (SQLite can't add them via create_all). Safe to call on boot."""
    additions = [
        ("video_jobs", "video_format", "VARCHAR(20) DEFAULT 'long'"),
        ("theme_queue", "video_format", "VARCHAR(20) DEFAULT 'long'"),
        ("platform_accounts", "music_style", "VARCHAR(20) DEFAULT 'balanced'"),
        ("platform_accounts", "video_source_mode", "VARCHAR(20) DEFAULT 'ai'"),
        ("platform_accounts", "drive_folder_id", "VARCHAR(160)"),
        ("platform_accounts", "drive_folder_url", "TEXT"),
        ("platform_accounts", "drive_niche", "VARCHAR(120)"),
        ("platform_accounts", "drive_recursive", "BOOLEAN DEFAULT true"),
        ("platform_accounts", "ride_trends", "BOOLEAN DEFAULT false"),
        ("platform_accounts", "trends_per_cycle", "INTEGER DEFAULT 1"),
        ("video_analytics", "watch_minutes", "INTEGER DEFAULT 0"),
        ("video_analytics", "avg_view_seconds", "FLOAT DEFAULT 0"),
        ("video_analytics", "avg_view_pct", "FLOAT DEFAULT 0"),
        ("video_analytics", "subscribers_gained", "INTEGER DEFAULT 0"),
        ("video_jobs", "orphan_resume_count", "INTEGER DEFAULT 0"),
        ("platform_accounts", "channel_optimization",
         "JSON DEFAULT '{}'" if not settings.sqlalchemy_url.startswith("sqlite") else "TEXT DEFAULT '{}'"),
    ]
    try:
        with engine.begin() as conn:
            for table, col, decl in additions:
                try:
                    if col not in _existing_columns(conn, table):
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {decl}"))
                        logger.info("Migração: coluna %s.%s adicionada.", table, col)
                except Exception as exc:  # noqa: BLE001  (e.g. table not created yet)
                    logger.debug("ensure_columns skip %s.%s: %s", table, col, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ensure_columns falhou (ignorada): %s", exc)


def ensure_indexes() -> None:
    """Idempotent index creation for the scheduler's hot query paths.

    `(status, scheduled_at)` is scanned by _job_publish_due every minute and
    `(account_id, created_at)` by the list/analytics endpoints; create_all() never
    adds indexes to a pre-existing table, so as video_jobs grows these become full
    scans. CREATE INDEX IF NOT EXISTS is a no-op once present (Postgres + SQLite)."""
    indexes = [
        ("ix_jobs_status_sched", "video_jobs", "status, scheduled_at", False),
        ("ix_jobs_account_created", "video_jobs", "account_id, created_at", False),
        ("ix_theme_queue_account_status", "theme_queue", "account_id, status", False),
        # Best-effort: if a pre-existing DB already has duplicate ScheduleConfig
        # rows for one account, this will fail and is safely ignored below — new
        # rows are still protected by the ORM-level check-then-insert fallback
        # in routers/schedule.upsert_config.
        ("uq_schedule_configs_account_id", "schedule_configs", "account_id", True),
    ]
    try:
        with engine.begin() as conn:
            for name, table, cols, unique in indexes:
                try:
                    kind = "UNIQUE INDEX" if unique else "INDEX"
                    conn.execute(text(f"CREATE {kind} IF NOT EXISTS {name} ON {table} ({cols})"))
                except Exception as exc:  # noqa: BLE001  (e.g. table not created yet)
                    logger.debug("ensure_indexes skip %s: %s", name, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ensure_indexes falhou (ignorada): %s", exc)


def reset_db() -> None:
    import backend.models  # noqa: F401

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("[OK] Database reset (all tables dropped and recreated)")


if __name__ == "__main__":
    # When run via `python -m backend.database`, this file is module `__main__`,
    # while `backend.models` imports `backend.database` as a *separate* module.
    # Delegate to the canonical module so models and create_all share one Base.
    from backend.database import init_db as _init_db
    from backend.database import reset_db as _reset_db

    parser = argparse.ArgumentParser(description="Database management")
    parser.add_argument("--init", action="store_true", help="Create all tables")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate (DEV)")
    args = parser.parse_args()

    if args.reset:
        _reset_db()
    else:
        # Default action is --init so `start.sh` can call it bare.
        _init_db()
