"""
ContentCalendarAgent — strategic scheduling (not just fixed clock times).

Modes:
  fixed          - use the account's configured post_times
  smart          - learn best hours from video_analytics (first-2h views)
  trending_aware - publish ASAP into the nearest free slot

Avoids cross-account collisions (15-min spacing) and respects daily quota.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import PlatformAccount, ScheduleConfig, VideoAnalytics, VideoJob

logger = logging.getLogger("studio.calendar")
SPACING = timedelta(minutes=15)
DEFAULT_TZ = "America/Sao_Paulo"

# General high-engagement posting windows (BR audience, YouTube/Shorts consensus),
# ranked. Used by "smart" mode UNTIL the account has its own analytics, and as the
# fallback when no post_times are configured. Once real first-2h view data exists,
# _smart_times() overrides these with the account's actually-best hours.
BEST_TIMES_RANKED = ["19:00", "21:00", "12:00", "18:00", "20:00", "15:00", "13:00", "08:00", "22:00", "17:00"]


def _utcnow() -> datetime:
    """Timezone-aware 'now' in UTC (replaces the old naive datetime.utcnow())."""
    return datetime.now(timezone.utc)


def _resolve_tz(name: str | None) -> ZoneInfo:
    """ZoneInfo for the account's local timezone, falling back to the default."""
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except Exception:  # noqa: BLE001 — unknown/invalid tz name
        logger.warning("Unknown timezone %r; falling back to %s.", name, DEFAULT_TZ)
        return ZoneInfo(DEFAULT_TZ)


class ContentCalendarAgent:
    def __init__(self, db: Session) -> None:
        self.db = db

    def next_slots(self, account_id: int, count: int = 5, mode: str | None = None) -> list[datetime]:
        acct = self.db.get(PlatformAccount, account_id)
        if not acct:
            return []
        cfg = self.db.execute(
            select(ScheduleConfig).where(ScheduleConfig.account_id == account_id)
        ).scalars().first()
        mode = mode or (cfg.mode if cfg else "fixed")
        per_day = (cfg.videos_per_day if cfg else None) or (acct.schedule or {}).get("videos_per_day", 1)
        tz = _resolve_tz(cfg.timezone if cfg else None)

        if mode == "trending_aware":
            return self._asap_slots(count)

        post_times = self.resolve_post_times(account_id, cfg, per_day, mode)
        return self._fixed_slots(post_times, per_day, count, tz)

    def resolve_post_times(self, account_id: int, cfg, per_day: int, mode: str | None = None) -> list[str]:
        """Effective HH:MM list for an account, resolving the schedule mode.

        smart  -> the account's learned best hours, or the general best-times spread
                  (BEST_TIMES_RANKED) until enough analytics exist.
        fixed  -> the configured post_times, or the best-times spread if none set.
        Used by both next_slots() and the scheduler's slot-due check so the calendar
        the user sees and the generation timing always agree.
        """
        mode = mode or (cfg.mode if cfg else "fixed")
        configured = list(cfg.post_times) if (cfg and cfg.post_times) else []
        if mode == "smart":
            return self._smart_times(account_id) or self.best_times(per_day)
        return configured or self.best_times(per_day)

    @staticmethod
    def best_times(n: int) -> list[str]:
        """Top-N general best posting times, time-sorted for a sane daily spread."""
        n = max(1, int(n or 1))
        return sorted(BEST_TIMES_RANKED[:n])

    def upcoming_slots(self, account_id: int, cfg, per_day: int, now_utc: datetime,
                       days: int = 3) -> list[datetime]:
        """Future slot datetimes (tz-aware UTC) from the account's resolved post_times,
        projected over the next `days`. No collision-nudging — the scheduler dedups by
        existing job scheduled_at, so each slot stays at its exact clock time (needed
        so YouTube's publishAt lands on the intended hour)."""
        from datetime import time as _time

        tz = _resolve_tz(cfg.timezone if cfg else None)
        hhmm = self.resolve_post_times(account_id, cfg, max(1, per_day))
        times = []
        for s in hhmm:
            try:
                h, m = str(s).split(":")
                times.append(_time(int(h), int(m)))
            except Exception:  # noqa: BLE001
                continue
        times = sorted(times)[: max(1, per_day)]
        day = now_utc.astimezone(tz).date()
        out: list[datetime] = []
        for d in range(days + 1):
            for t in times:
                slot = datetime.combine(day + timedelta(days=d), t, tzinfo=tz).astimezone(timezone.utc)
                if slot > now_utc:
                    out.append(slot)
        return sorted(out)

    def _fixed_slots(
        self, post_times: list[str], per_day: int, count: int, tz: ZoneInfo
    ) -> list[datetime]:
        """
        Interpret each HH:MM in the account's LOCAL timezone, then convert to UTC.

        '19:00' with tz=America/Sao_Paulo means 19:00 local — we build the local
        datetime, attach the local tzinfo, then `.astimezone(utc)`. So the stored
        scheduled_at is the correct UTC instant (22:00Z in BRT), fixing the old
        3h drift where naive HH:MM was treated as if it were already UTC.
        """
        times = sorted(self._parse(t) for t in post_times)[: max(1, per_day)]
        now = _utcnow()
        slots: list[datetime] = []
        day = now.astimezone(tz).date()  # 'today' as seen in the account's timezone
        guard = 0
        while len(slots) < count and guard < 60:
            for t in times:
                local_dt = datetime.combine(day, t, tzinfo=tz)
                dt = local_dt.astimezone(timezone.utc)
                if dt > now:
                    slots.append(self._avoid_collision(dt))
                    if len(slots) >= count:
                        break
            day += timedelta(days=1)
            guard += 1
        return slots

    def _asap_slots(self, count: int) -> list[datetime]:
        base = _utcnow() + timedelta(minutes=5)
        return [self._avoid_collision(base + i * timedelta(hours=2)) for i in range(count)]

    def _smart_times(self, account_id: int) -> list[str]:
        """Top posting hours by average first-2h views for this account's jobs."""
        rows = self.db.execute(
            select(VideoAnalytics.collected_at, VideoAnalytics.views)
            .join(VideoJob, VideoJob.id == VideoAnalytics.job_id)
            .where(VideoJob.account_id == account_id, VideoAnalytics.snapshot_type == "2h")
        ).all()
        if not rows:
            return []
        by_hour: dict[int, list[int]] = {}
        for collected_at, views in rows:
            if collected_at:
                by_hour.setdefault(collected_at.hour, []).append(views or 0)
        ranked = sorted(by_hour.items(), key=lambda kv: sum(kv[1]) / len(kv[1]), reverse=True)
        return [f"{h:02d}:00" for h, _ in ranked[:3]]

    def _avoid_collision(self, dt: datetime) -> datetime:
        """
        Nudge later if another job is scheduled within SPACING.

        Returns a tz-aware UTC datetime. The query bounds are made NAIVE-UTC
        because VideoJob.scheduled_at is a naive DateTime column persisting UTC
        wall-clock — comparing a naive column against tz-aware bounds raises in
        most drivers, so we strip tzinfo for the comparison only.
        """
        for _ in range(20):
            lo = (dt - SPACING).astimezone(timezone.utc).replace(tzinfo=None)
            hi = (dt + SPACING).astimezone(timezone.utc).replace(tzinfo=None)
            clash = self.db.execute(
                select(VideoJob.id).where(
                    VideoJob.scheduled_at.isnot(None),
                    VideoJob.scheduled_at >= lo,
                    VideoJob.scheduled_at <= hi,
                )
            ).first()
            if not clash:
                return dt
            dt += SPACING
        return dt

    @staticmethod
    def _parse(hhmm: str) -> time:
        try:
            h, m = hhmm.split(":")
            return time(int(h), int(m))
        except Exception:
            return time(19, 0)


if __name__ == "__main__":
    from backend.database import SessionLocal

    db = SessionLocal()
    cal = ContentCalendarAgent(db)
    acct = db.execute(select(PlatformAccount)).scalars().first()
    if acct:
        print("next slots:", [s.isoformat() for s in cal.next_slots(acct.id, 5)])
    else:
        print("No accounts to schedule.")
    db.close()
