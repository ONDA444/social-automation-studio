"""Importing this package registers every mapper on Base.metadata."""
from backend.models.platform_account import PlatformAccount  # noqa: F401
from backend.models.schedule_config import ScheduleConfig  # noqa: F401
from backend.models.theme_queue import ThemeQueue  # noqa: F401
from backend.models.video_analytics import VideoAnalytics  # noqa: F401
from backend.models.video_job import JobStatus, VideoJob  # noqa: F401

__all__ = [
    "VideoJob",
    "JobStatus",
    "PlatformAccount",
    "ScheduleConfig",
    "ThemeQueue",
    "VideoAnalytics",
]
