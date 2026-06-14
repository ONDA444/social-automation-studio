"""FIFO queue of themes/titles to be consumed by the content pipeline."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class ThemeQueue(Base):
    __tablename__ = "theme_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Nullable: a theme can be unassigned, and is detached (not deleted) if its
    # account is removed.
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_accounts.id", ondelete="SET NULL"), default=None
    )

    theme: Mapped[str] = mapped_column(Text)  # the theme / title text

    content_type: Mapped[str] = mapped_column(
        String(60), default="film_recap_ai_images"
    )
    target_platforms: Mapped[list] = mapped_column(
        JSON, default=lambda: ["youtube"]
    )

    # pending | consumed — indexed for fast FIFO pop of the next pending theme.
    status: Mapped[str] = mapped_column(String(20), index=True, default="pending")
    position: Mapped[int] = mapped_column(Integer, default=0)  # FIFO ordering

    consumed_job_id: Mapped[int | None] = mapped_column(Integer, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "account_id": self.account_id,
            "theme": self.theme,
            "content_type": self.content_type,
            "target_platforms": self.target_platforms or [],
            "status": self.status,
            "position": self.position,
            "consumed_job_id": self.consumed_job_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
