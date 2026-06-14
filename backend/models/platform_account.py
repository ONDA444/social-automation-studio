"""A connected channel/account = a dedicated workspace (queue, schedule, config)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class PlatformAccount(Base):
    __tablename__ = "platform_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    platform: Mapped[str] = mapped_column(String(20), index=True)  # youtube|tiktok|instagram
    display_name: Mapped[str] = mapped_column(String(120))
    channel_id: Mapped[str | None] = mapped_column(String(120), default=None)

    # OAuth tokens etc., Fernet-encrypted (never stored in plaintext).
    credentials_encrypted: Mapped[str | None] = mapped_column(Text, default=None)

    # --- Content identity ---
    niche: Mapped[str | None] = mapped_column(String(120), default=None)
    target_audience: Mapped[str | None] = mapped_column(Text, default=None)
    preferred_templates: Mapped[list] = mapped_column(JSON, default=list)
    preferred_voice: Mapped[str] = mapped_column(String(60), default="pt-BR-AntonioNeural")
    content_tone: Mapped[str] = mapped_column(String(60), default="neutral")
    avoid_topics: Mapped[list] = mapped_column(JSON, default=list)
    content_language: Mapped[str] = mapped_column(String(10), default="pt-BR")

    # --- Scheduling (denormalised convenience copy; canonical config in ScheduleConfig) ---
    schedule: Mapped[dict] = mapped_column(JSON, default=dict)

    # --- Cross-platform linking ---
    linked_accounts: Mapped[dict] = mapped_column(JSON, default=dict)  # {tiktok: id, instagram: id}
    mirror_to_linked: Mapped[bool] = mapped_column(default=False)
    mirror_format: Mapped[str] = mapped_column(String(20), default="shorts_30s")

    # --- Quota / status ---
    quota_used_today: Mapped[int] = mapped_column(Integer, default=0)
    quota_limit: Mapped[int] = mapped_column(Integer, default=10000)
    status: Mapped[str] = mapped_column(String(20), default="active")
    # active | paused | quota_exceeded | auth_error

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    jobs = relationship(
        "VideoJob", back_populates="account", foreign_keys="VideoJob.account_id"
    )
    schedule_configs = relationship(
        "ScheduleConfig", back_populates="account", cascade="all, delete-orphan"
    )

    def to_dict(self, include_secrets: bool = False) -> dict:
        data = {
            "id": self.id,
            "platform": self.platform,
            "display_name": self.display_name,
            "channel_id": self.channel_id,
            "niche": self.niche,
            "target_audience": self.target_audience,
            "preferred_templates": self.preferred_templates or [],
            "preferred_voice": self.preferred_voice,
            "content_tone": self.content_tone,
            "avoid_topics": self.avoid_topics or [],
            "content_language": self.content_language,
            "schedule": self.schedule or {},
            "linked_accounts": self.linked_accounts or {},
            "mirror_to_linked": self.mirror_to_linked,
            "mirror_format": self.mirror_format,
            "quota_used_today": self.quota_used_today,
            "quota_limit": self.quota_limit,
            "status": self.status,
            "has_credentials": bool(self.credentials_encrypted),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_secrets:
            data["credentials_encrypted"] = self.credentials_encrypted
        return data
