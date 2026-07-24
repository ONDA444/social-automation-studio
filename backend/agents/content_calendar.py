"""
ContentCalendarAgent — strategic scheduling (not just fixed clock times).

Modes:
  fixed          - use the account's configured post_times
  smart          - learn best hours from video_analytics (first-2h views)
  trending_aware - hands-off auto-spread: videos_per_day posts at the account's
                   learned-best hours (or the general best-times spread) — the
                   calendar the user SEES is exactly what the scheduler generates.

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
_BEST_TIME_SCORE = {hhmm: len(BEST_TIMES_RANKED) - idx for idx, hhmm in enumerate(BEST_TIMES_RANKED)}

# Minimum number of videos with real first-2h views before "smart" learning is
# trusted over the general best-times spread. Below this the per-hour ranking is
# noise (a single early view would peg a random hour). Mirrors the analytics
# learning loop's gate so the system never steers on too little data.
_SMART_MIN_MEASURED = 3


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

        # ALL modes resolve to anchored clock slots (trending_aware/smart pick their
        # hours automatically — see resolve_post_times). Using the same path for display
        # and generation means the calendar the user sees is exactly what the scheduler
        # produces & posts — no more floating ASAP slots that never generated anything.
        post_times = self.resolve_post_times(account_id, cfg, per_day, mode)
        return self._fixed_slots(post_times, per_day, count, tz)

    def resolve_post_times(self, account_id: int, cfg, per_day: int, mode: str | None = None) -> list[str]:
        """Effective HH:MM list for an account, resolving the schedule mode.

        smart / trending_aware -> hands-off: the account's learned best hours, or the
                  general best-times spread (BEST_TIMES_RANKED) until analytics exist.
                  Always returns a per_day-sized spread, so videos_per_day is honored
                  even if the account still has a stale single post_time saved.
        fixed  -> the configured post_times, or the best-times spread if none set.
        Used by both next_slots() and the scheduler's slot-due check so the calendar
        the user sees and the generation timing always agree.
        """
        mode = mode or (cfg.mode if cfg else "fixed")
        configured = list(cfg.post_times) if (cfg and cfg.post_times) else []
        if mode in ("smart", "trending_aware"):
            tz = _resolve_tz(cfg.timezone if cfg else None)
            learned = self._smart_times(account_id, tz)
            # Proven hours (from _SMART_MIN_MEASURED+ real measured videos) bypass
            # the anti-cluster gap filter entirely — they're already validated by
            # real data, not a guess _spread_times needs to protect against
            # clustering. BR prime time is typically a contiguous block (e.g.
            # 20:00 and 21:00, 1h apart): running the gap filter over learned+
            # BEST_TIMES_RANKED together used to drop the 2nd learned hour just
            # for being <2h from the 1st, replacing a proven hour with a generic
            # one that never had a single measured view on this account. Only
            # run the gap filter on the BEST_TIMES_RANKED filler needed to reach
            # per_day, never on the learned hours themselves.
            learned_ranked = learned[:per_day]
            extra_needed = per_day - len(learned_ranked)
            filler = (
                self._spread_times(
                    [t for t in BEST_TIMES_RANKED if t not in learned_ranked], extra_needed
                )
                if extra_needed > 0 else []
            )
            return sorted(learned_ranked + filler)[:per_day]
        return configured or self.best_times(per_day)

    @staticmethod
    def best_times(n: int) -> list[str]:
        """Top-N general best posting times, time-sorted for a sane daily spread."""
        n = max(1, int(n or 1))
        return ContentCalendarAgent._spread_times(BEST_TIMES_RANKED, n)

    @staticmethod
    def _spread_times(candidates: list[str], n: int, min_gap_hours: int = 2) -> list[str]:
        """Pick ranked times while avoiding cramped daily clusters when possible."""
        n = max(1, int(n or 1))
        ordered = list(dict.fromkeys(t for t in candidates if isinstance(t, str)))
        selected: list[str] = []

        def hour(hhmm: str) -> int | None:
            try:
                return int(hhmm.split(":", 1)[0])
            except Exception:
                return None

        for candidate in ordered:
            h = hour(candidate)
            if h is None:
                continue
            if all(abs(h - (hour(existing) or h)) >= min_gap_hours for existing in selected):
                selected.append(candidate)
            if len(selected) >= n:
                break
        for candidate in ordered:
            if candidate not in selected:
                selected.append(candidate)
            if len(selected) >= n:
                break
        return sorted(selected[:n])

    def upcoming_slots(self, account_id: int, cfg, per_day: int, now_utc: datetime,
                       days: int = 3) -> list[datetime]:
        """Future slot datetimes (tz-aware UTC) from the account's resolved post_times,
        projected over the next `days`. No collision-nudging — the scheduler dedups by
        existing job scheduled_at, so each slot stays at its exact clock time (needed
        so YouTube's publishAt lands on the intended hour)."""
        from datetime import time as _time

        tz = _resolve_tz(cfg.timezone if cfg else None)
        hhmm = self.resolve_post_times(account_id, cfg, max(1, per_day))
        # Same fix as _fixed_slots: slice to per_day BEFORE sorting, so a
        # per_day cut keeps the configured/priority-ordered hours instead of
        # always keeping whichever hours happen to be chronologically first.
        times = []
        for s in hhmm[: max(1, per_day)]:
            try:
                h, m = str(s).split(":")
                times.append(_time(int(h), int(m)))
            except Exception:  # noqa: BLE001
                continue
        times = sorted(times)
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
        # Slice to per_day BEFORE sorting by clock time. Sorting first and then
        # slicing silently keeps only the chronologically-earliest slots — if an
        # admin configured post_times=['08:00','12:00','19:00','21:00'] but
        # videos_per_day is 2, that used to always post at 08:00/12:00 and
        # permanently drop the configured prime-time hours (19h/21h), with no
        # log or warning that the peak times stopped being used.
        times = sorted(self._parse(t) for t in post_times[: max(1, per_day)])
        now = _utcnow()
        slots: list[datetime] = []
        day = now.astimezone(tz).date()  # 'today' as seen in the account's timezone
        guard = 0
        while len(slots) < count and guard < 60:
            for t in times:
                local_dt = datetime.combine(day, t, tzinfo=tz)
                dt = local_dt.astimezone(timezone.utc)
                if dt > now:
                    slots.append(dt)
                    if len(slots) >= count:
                        break
            day += timedelta(days=1)
            guard += 1
        return slots

    def _smart_times(self, account_id: int, tz: "ZoneInfo | None" = None) -> list[str]:
        """Top posting hours (in account LOCAL timezone) by avg first-2h views.

        Returns [] until at least _SMART_MIN_MEASURED videos have real (>0) first-2h
        views — below that the ranking is pure noise (one early view would peg a random
        hour like 06:00). The caller falls back to the general best-times spread.
        """
        rows = self.db.execute(
            select(VideoJob.scheduled_at, VideoJob.created_at, VideoAnalytics.collected_at, VideoAnalytics.views)
            .join(VideoJob, VideoJob.id == VideoAnalytics.job_id)
            .where(VideoJob.account_id == account_id, VideoAnalytics.snapshot_type == "2h")
        ).all()
        measured = [
            (scheduled_at or created_at or collected_at, views)
            for scheduled_at, created_at, collected_at, views in rows
            if (scheduled_at or created_at or collected_at) and (views or 0) > 0
        ]
        if len(measured) < _SMART_MIN_MEASURED:
            return []  # not enough signal — let best_times decide
        local_tz = tz or ZoneInfo(DEFAULT_TZ)
        by_hour: dict[int, list[int]] = {}
        for published_at, views in measured:
            if published_at:
                # collected_at is stored as naive UTC — attach UTC tzinfo so
                # astimezone() converts correctly to the account's local timezone.
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
                local_hour = published_at.astimezone(local_tz).hour
                by_hour.setdefault(local_hour, []).append(views or 0)
        ranked = sorted(by_hour.items(), key=lambda kv: self._smart_hour_score(kv[0], kv[1]), reverse=True)
        return [f"{h:02d}:00" for h, _ in ranked[:6]]

    @staticmethod
    def _smart_hour_score(hour: int, views: list[int]) -> float:
        hhmm = f"{hour:02d}:00"
        prior = _BEST_TIME_SCORE.get(hhmm, 4)
        prior_weight = 2
        return (sum(views) + prior * prior_weight) / (len(views) + prior_weight)

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
