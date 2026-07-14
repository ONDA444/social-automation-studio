"""Per-account scheduling strategy (fixed / smart / trending_aware)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class ScheduleConfig(Base):
    __tablename__ = "schedule_configs"
    __table_args__ = (
        # One config per account: without this, two concurrent PUT
        # /schedule/config/{account_id} requests can both see "no existing row"
        # and each insert their own, leaving a duplicate.
        UniqueConstraint("account_id", name="uq_schedule_configs_account_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("platform_accounts.id", ondelete="CASCADE"), index=True
    )

    mode: Mapped[str] = mapped_column(String(20), default="fixed")  # fixed|smart|trending_aware
    timezone: Mapped[str] = mapped_column(String(60), default="America/Sao_Paulo")
    videos_per_day: Mapped[int] = mapped_column(Integer, default=1)
    post_times: Mapped[list] = mapped_column(JSON, default=list)  # ["19:00", "21:00"]

    auto_shorts: Mapped[bool] = mapped_column(default=True)
    shorts_formats: Mapped[list] = mapped_column(JSON, default=list)  # [1,2,3,4,5]

    # smart-mode learned best slots, cached from analytics
    learned_best_slots: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    account = relationship("PlatformAccount", back_populates="schedule_configs")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "account_id": self.account_id,
            "mode": self.mode,
            "timezone": self.timezone,
            "videos_per_day": self.videos_per_day,
            "post_times": self.post_times or [],
            "auto_shorts": self.auto_shorts,
            "shorts_formats": self.shorts_formats or [],
            "learned_best_slots": self.learned_best_slots or [],
        }
