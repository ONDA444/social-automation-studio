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
from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import PlatformAccount, ScheduleConfig, VideoAnalytics, VideoJob

logger = logging.getLogger("studio.calendar")
SPACING = timedelta(minutes=15)


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
        post_times = (cfg.post_times if cfg else None) or (acct.schedule or {}).get("post_times", ["19:00"])

        if mode == "smart":
            post_times = self._smart_times(account_id) or post_times
        if mode == "trending_aware":
            return self._asap_slots(count)

        return self._fixed_slots(post_times, per_day, count)

    def _fixed_slots(self, post_times: list[str], per_day: int, count: int) -> list[datetime]:
        times = sorted(self._parse(t) for t in post_times)[: max(1, per_day)]
        slots: list[datetime] = []
        day = datetime.utcnow().date()
        guard = 0
        while len(slots) < count and guard < 60:
            for t in times:
                dt = datetime.combine(day, t)
                if dt > datetime.utcnow():
                    slots.append(self._avoid_collision(dt))
                    if len(slots) >= count:
                        break
            day += timedelta(days=1)
            guard += 1
        return slots

    def _asap_slots(self, count: int) -> list[datetime]:
        base = datetime.utcnow() + timedelta(minutes=5)
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
        """Nudge later if another job is scheduled within SPACING."""
        for _ in range(20):
            clash = self.db.execute(
                select(VideoJob.id).where(
                    VideoJob.scheduled_at.isnot(None),
                    VideoJob.scheduled_at >= dt - SPACING,
                    VideoJob.scheduled_at <= dt + SPACING,
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
