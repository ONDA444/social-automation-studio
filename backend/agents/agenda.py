"""Agenda generation for a Channel's PublishSession.

Given a Channel and a target date, ensures that day's PublishSession exists
and creates VideoJob rows -- via the SAME Drive-reservation path the
automatic scheduler already uses (scheduler._try_create_ready_video_job) --
up to the channel's daily_limit_long/daily_limit_short, spaced across
posting_window_start/end. This is deliberately NOT wired into the automatic
APScheduler ticks in scheduler.py; it's a separate, explicit action (endpoint
or CLI) so an operator decides when a channel's day gets planned, instead of
a second automatic queue running alongside the existing one.

Idempotent and safe to run alongside the automatic ThemeQueue-driven
scheduler on the SAME account: caps are checked against every VideoJob
already scheduled that day for the account/format, not just this session's
own planned_items, so the two mechanisms can't jointly blow past a channel's
daily limit.
"""
from __future__ import annotations

import logging
from datetime import date as date_, datetime, time as time_, timedelta
from types import SimpleNamespace

from sqlalchemy import func, select

from backend.models import Channel, JobStatus, PublishSession, VideoJob

logger = logging.getLogger("studio.agenda")


def get_or_create_session(db, channel: Channel, target_date: date_) -> PublishSession:
    session = db.execute(
        select(PublishSession).where(
            PublishSession.channel_id == channel.id, PublishSession.date == target_date
        )
    ).scalars().first()
    if session is None:
        session = PublishSession(channel_id=channel.id, date=target_date, status="pending")
        db.add(session)
        db.flush()
    return session


def _day_bounds(target_date: date_) -> tuple[datetime, datetime]:
    return (datetime.combine(target_date, time_.min), datetime.combine(target_date, time_.max))


def _count_scheduled(db, account_id: int, target_date: date_, video_format: str) -> int:
    """Every non-errored job scheduled that day for this account/format --
    regardless of whether it came from this agenda generator, the automatic
    ThemeQueue scheduler, or a manual upload. This is what keeps the two
    systems from double-booking the same channel's day."""
    start, end = _day_bounds(target_date)
    return db.execute(
        select(func.count(VideoJob.id)).where(
            VideoJob.account_id == account_id,
            VideoJob.video_format == video_format,
            VideoJob.scheduled_at >= start,
            VideoJob.scheduled_at <= end,
            VideoJob.status != JobStatus.ERROR,
        )
    ).scalar() or 0


def _parse_hhmm(value: str) -> time_:
    hh, mm = (value or "08:00").split(":")
    return time_(int(hh), int(mm))


def _slots_within_window(target_date: date_, start: str, end: str, count: int) -> list[datetime]:
    """Evenly space `count` slots inside [start, end) on target_date."""
    if count <= 0:
        return []
    start_dt = datetime.combine(target_date, _parse_hhmm(start))
    end_dt = datetime.combine(target_date, _parse_hhmm(end))
    if end_dt <= start_dt:
        end_dt = start_dt  # degenerate/inverted window -> stack everything at start
    if count == 1:
        return [start_dt]
    step = (end_dt - start_dt).total_seconds() / count
    return [start_dt + timedelta(seconds=step * i) for i in range(count)]


def generate_daily_agenda(db, channel: Channel, target_date: date_) -> PublishSession:
    """Idempotent: safe to call repeatedly for the same channel/date -- only
    creates the jobs still missing to reach the channel's daily caps. Runs
    the SAME synchronous download+curation+dispatch work
    _try_create_ready_video_job always does -- callers on a request thread
    must run this in a background thread (see routers/channels.py)."""
    from backend.models import PlatformAccount
    from backend.scheduler import _try_create_ready_video_job

    session = get_or_create_session(db, channel, target_date)
    acct = db.get(PlatformAccount, channel.account_id)
    if acct is None or not channel.active:
        return session

    created_ids: list[int] = []
    for video_format, limit in (
        ("long", channel.daily_limit_long), ("short", channel.daily_limit_short),
    ):
        if limit <= 0:
            continue
        already = _count_scheduled(db, channel.account_id, target_date, video_format)
        needed = max(0, limit - already)
        if not needed:
            continue
        slots = _slots_within_window(
            target_date, channel.posting_window_start, channel.posting_window_end, needed
        )
        fake_theme = SimpleNamespace(
            theme=None, content_type="film_recap_ai_images",
            video_format=video_format, target_platforms=["youtube"],
        )
        for slot in slots:
            job_id = _try_create_ready_video_job(db, acct, slot, theme=fake_theme, slot_key=None)
            if job_id:
                created_ids.append(job_id)
            else:
                logger.info(
                    "agenda: sem video pronto disponivel p/ canal %s (%s, %s) -- parando aqui",
                    channel.id, video_format, target_date,
                )
                break  # Drive inventory exhausted for this format; more slots won't help

    if created_ids:
        session.planned_items = [*(session.planned_items or []), *created_ids]
        for jid in created_ids:
            job = db.get(VideoJob, jid)
            if job:
                job.publish_session_id = session.id
        if session.status == "pending":
            session.status = "running"
        db.commit()
        db.refresh(session)
    logger.info(
        "agenda_generated channel_id=%s date=%s created=%d planned_total=%d",
        channel.id, target_date, len(created_ids), len(session.planned_items or []),
    )
    return session


def sync_published_items(db, session: PublishSession) -> PublishSession:
    """Recompute published_items/status from the actual VideoJob state --
    call after a publish tick so the session reflects reality without the
    dispatch/publish pipeline needing to know PublishSession exists."""
    if not session.planned_items:
        return session
    rows = db.execute(
        select(VideoJob.id, VideoJob.status).where(VideoJob.id.in_(session.planned_items))
    ).all()
    published = [jid for jid, status in rows if status == JobStatus.PUBLISHED]
    errored = [jid for jid, status in rows if status == JobStatus.ERROR]
    session.published_items = published
    total = len(session.planned_items)
    if len(published) == total:
        session.status = "completed"
        session.finished_at = session.finished_at or datetime.utcnow()
    elif errored and (len(published) + len(errored)) == total:
        session.status = "partial_failure"
        session.finished_at = session.finished_at or datetime.utcnow()
    elif published or errored:
        session.status = "running"
        session.started_at = session.started_at or datetime.utcnow()
    db.commit()
    db.refresh(session)
    return session
