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
    first created (SQLite can't add them via create_all). Safe to call on boot.

    This -- plus ensure_indexes()/ensure_constraints() below -- is the project's
    actual migration mechanism. There is no alembic (no alembic.ini, no
    versions/ folder, nothing to `alembic upgrade head`): every schema change is
    a new tuple appended here, applied on every boot via a plain ALTER TABLE
    guarded by an "if column not already there" check. That works identically
    against SQLite (dev) and Postgres (Railway) and needs no separate
    migration-run step or downtime window."""
    additions = [
        ("video_jobs", "video_format", "VARCHAR(20) DEFAULT 'long'"),
        ("theme_queue", "video_format", "VARCHAR(20) DEFAULT 'long'"),
        ("platform_accounts", "music_style", "VARCHAR(20) DEFAULT 'balanced'"),
        ("platform_accounts", "channel_stage", "VARCHAR(20) DEFAULT 'growing'"),
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
        ("video_jobs", "ready_video_attempt_count", "INTEGER DEFAULT 0"),
        ("video_jobs", "schedule_slot_key", "VARCHAR(160)"),
        ("video_jobs", "publish_session_id", "INTEGER"),
        ("channels", "intro_mode", "VARCHAR(20) DEFAULT 'mixed'"),
        ("platform_accounts", "channel_optimization",
         "JSON DEFAULT '{}'" if not settings.sqlalchemy_url.startswith("sqlite") else "TEXT DEFAULT '{}'"),
        # Manually-logged YouTube copyright strikes -- no reliable API for this,
        # the operator checks YouTube Studio by hand and records it here.
        ("platform_accounts", "copyright_strikes", "INTEGER DEFAULT 0"),
        ("platform_accounts", "copyright_notes", "TEXT"),
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

    `(status, scheduled_at)` is scanned by _job_publish_due every minute,
    `(account_id, created_at)` by the list/analytics endpoints, and
    `(status, updated_at)` by the stuck-job recovery sweeps every 10-20 min;
    create_all() never adds indexes to a pre-existing table, so as video_jobs
    grows these become full scans. CREATE INDEX IF NOT EXISTS is a no-op once
    present (Postgres + SQLite)."""
    indexes = [
        ("ix_jobs_status_sched", "video_jobs", "status, scheduled_at", False),
        ("ix_jobs_account_created", "video_jobs", "account_id, created_at", False),
        # Scanned every 10-20 min by the stuck-job recovery sweeps
        # (_job_retry_errored, _job_recover_stuck_publishing,
        # _job_recover_stuck_queued in scheduler.py), all of which filter by
        # status + updated_at (the latter also ORDER BY updated_at).
        ("ix_jobs_status_updated", "video_jobs", "status, updated_at", False),
        ("ix_theme_queue_account_status", "theme_queue", "account_id, status", False),
        # Best-effort: if a pre-existing DB already has duplicate ScheduleConfig
        # rows for one account, this will fail and is safely ignored below — new
        # rows are still protected by the ORM-level check-then-insert fallback
        # in routers/schedule.upsert_config.
        ("uq_schedule_configs_account_id", "schedule_configs", "account_id", True),
        # Enforces the scheduler's slot dedup at the DB level (NULLs — manual/
        # immediate/mirror jobs — don't participate in the uniqueness check).
        ("uq_jobs_schedule_slot_key", "video_jobs", "schedule_slot_key", True),
        # Prevents the same external channel from being connected twice on the
        # same platform (two PlatformAccount rows each running their own
        # ScheduleConfig/quota and publishing independently to one real channel).
        # Partial so accounts not yet connected (channel_id IS NULL) never
        # collide with each other.
        ("uq_platform_accounts_platform_channel_id", "platform_accounts",
         "platform, channel_id", True, "channel_id IS NOT NULL"),
        ("ix_jobs_publish_session", "video_jobs", "publish_session_id", False),
        # mirror_after_publish (agents/cross_platform_linker.py) filters on this
        # column every time a job publishes on an account with mirror_to_linked
        # on; model-level index=True only applies to freshly created tables.
        ("ix_jobs_mirror_source", "video_jobs", "mirror_source_id", False),
        # agents/drive_library.py's _reserve_matching filters on exactly this
        # triple (status='available' AND (account_id IS NULL OR = X) AND
        # video_format=Y) every scheduler slot-fill tick for accounts on
        # drive/mixed video_source_mode; the account_id OR-NULL branches each
        # still use the full 3-column prefix (Postgres turns "col IS NULL OR
        # col = X" into two index scans BitmapOr'd together), so this beats
        # falling back to the single-column indexes on status/account_id/niche.
        ("ix_ready_videos_status_account_format", "ready_videos",
         "status, account_id, video_format", False),
        # Best-effort: if a pre-existing DB already has duplicate VideoAnalytics
        # rows for one (job_id, platform, snapshot_type), this will fail and is
        # safely ignored below — new rows are still protected by the ORM-level
        # select-then-insert upsert in agents/analytics.py.
        ("uq_video_analytics_job_platform_snapshot", "video_analytics",
         "job_id, platform, snapshot_type", True),
        # Same as ix_jobs_mirror_source above: consumed_job_id's model-level
        # index=True (and its FK) only apply to freshly created tables, not
        # this pre-existing theme_queue column.
        ("ix_theme_queue_consumed_job", "theme_queue", "consumed_job_id", False),
    ]
    try:
        with engine.begin() as conn:
            for spec in indexes:
                name, table, cols, unique = spec[:4]
                where = spec[4] if len(spec) > 4 else None
                try:
                    kind = "UNIQUE INDEX" if unique else "INDEX"
                    stmt = f"CREATE {kind} IF NOT EXISTS {name} ON {table} ({cols})"
                    if where:
                        stmt += f" WHERE {where}"
                    conn.execute(text(stmt))
                except Exception as exc:  # noqa: BLE001  (e.g. table not created yet)
                    logger.debug("ensure_indexes skip %s: %s", name, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ensure_indexes falhou (ignorada): %s", exc)

    # Legacy single-column index superseded by ix_jobs_status_sched above: status
    # is that composite index's leading column, so it already serves any
    # WHERE status = X on its own. The model no longer declares index=True for
    # status (see models/video_job.py) but create_all() never drops indexes from
    # a pre-existing table, so a DB created before this change still carries it
    # and pays 2x write cost on every status transition for no read benefit.
    try:
        with engine.begin() as conn:
            try:
                conn.execute(text("DROP INDEX IF EXISTS ix_video_jobs_status"))
            except Exception as exc:  # noqa: BLE001  (e.g. table not created yet)
                logger.debug("ensure_indexes drop skip ix_video_jobs_status: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ensure_indexes drop falhou (ignorada): %s", exc)


def ensure_constraints() -> None:
    """Idempotent CHECK constraint creation for status columns that, until now,
    were only enforced by application code (see SESSION_STATUSES in
    models/publish_session.py). create_all() only applies a new __table_args__
    entry to tables it creates fresh, so a publish_sessions table that already
    existed before the constraint was added needs this ALTER TABLE once at boot.

    SQLite can't add a CHECK constraint to an existing table at all (its ALTER
    TABLE is too limited) -- skipped there entirely, harmlessly: any SQLite DB
    created after this change already has the constraint via create_all()."""
    if settings.sqlalchemy_url.startswith("sqlite"):
        return
    from backend.models.publish_session import _SESSION_STATUS_LIST_SQL

    try:
        with engine.begin() as conn:
            exists = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.table_constraints "
                    "WHERE table_name = 'publish_sessions' "
                    "AND constraint_name = 'ck_publish_sessions_status'"
                )
            ).fetchone()
            if not exists:
                conn.execute(text(
                    "ALTER TABLE publish_sessions ADD CONSTRAINT ck_publish_sessions_status "
                    f"CHECK (status IN ({_SESSION_STATUS_LIST_SQL}))"
                ))
                logger.info("Migração: CHECK constraint ck_publish_sessions_status adicionada.")
    except Exception as exc:  # noqa: BLE001  (e.g. table not created yet, or pre-existing bad data)
        logger.warning("ensure_constraints falhou (ignorada): %s", exc)


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
