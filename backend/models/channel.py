"""Channel — the operational layer on top of a PlatformAccount.

PlatformAccount already owns identity/OAuth/quota (see its own docstring:
"A connected channel/account = a dedicated workspace"). Channel does NOT
duplicate that -- it holds only the orchestration config that account never
had: a visual identity applied to every curation overlay/intro card, and
explicit daily long/short caps + a posting window (replacing the single
combined `videos_per_day` a channel used to share with its ScheduleConfig).
One Channel per PlatformAccount (enforced by the unique account_id).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base

# Falls back to the channel's niche via visual_style/packaging.thumbnail_style
# shape already defined in scriptwriter.py's CHANNEL_DEFAULTS -- reused here
# instead of inventing a second schema for the same concept.
DEFAULT_VISUAL_THEME: dict = {
    "accent_color": "#FFD400",
    "stroke_color": "#000000",
    "text_color": "#FFFFFF",
    "font": "VeraBd.ttf",
    "stroke_width_ratio": 0.0065,  # of frame width, matches ready_video_curation.py's w // 340
}


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("platform_accounts.id", ondelete="CASCADE"), unique=True, index=True
    )

    name: Mapped[str] = mapped_column(String(120))
    youtube_channel_id: Mapped[str | None] = mapped_column(String(120), default=None)
    niche: Mapped[str | None] = mapped_column(String(120), default=None)
    tts_voice: Mapped[str] = mapped_column(String(60), default="pt-BR-AntonioNeural")

    # {accent_color, stroke_color, text_color, font, stroke_width_ratio} -- see
    # DEFAULT_VISUAL_THEME above. Consumed by ready_video_curation.py so every
    # overlay/intro card for this channel shares one visual identity instead of
    # the previous hardcoded yellow-on-black used for every channel alike.
    visual_theme: Mapped[dict] = mapped_column(JSON, default=dict)

    daily_limit_long: Mapped[int] = mapped_column(Integer, default=1)
    daily_limit_short: Mapped[int] = mapped_column(Integer, default=3)
    # "HH:MM" 24h, channel-local time (see ScheduleConfig.timezone for the
    # account's zone -- Channel doesn't repeat it, it's looked up via account).
    posting_window_start: Mapped[str] = mapped_column(String(5), default="08:00")
    posting_window_end: Mapped[str] = mapped_column(String(5), default="23:00")

    active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    account = relationship("PlatformAccount")
    sessions = relationship("PublishSession", back_populates="channel", cascade="all, delete-orphan")

    def resolved_visual_theme(self) -> dict:
        return {**DEFAULT_VISUAL_THEME, **(self.visual_theme or {})}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "account_id": self.account_id,
            "name": self.name,
            "youtube_channel_id": self.youtube_channel_id,
            "niche": self.niche,
            "tts_voice": self.tts_voice,
            "visual_theme": self.resolved_visual_theme(),
            "daily_limit_long": self.daily_limit_long,
            "daily_limit_short": self.daily_limit_short,
            "posting_window_start": self.posting_window_start,
            "posting_window_end": self.posting_window_end,
            "active": self.active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
