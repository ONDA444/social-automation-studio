"""Importing this package registers every mapper on Base.metadata."""
from backend.models.app_setting import AppSetting  # noqa: F401
from backend.models.platform_account import PlatformAccount  # noqa: F401
from backend.models.channel import Channel  # noqa: F401
from backend.models.drive_connection import DriveConnection  # noqa: F401
from backend.models.publish_session import PublishSession  # noqa: F401
from backend.models.ready_video import ReadyVideo  # noqa: F401
from backend.models.schedule_config import ScheduleConfig  # noqa: F401
from backend.models.theme_queue import ThemeQueue  # noqa: F401
from backend.models.video_analytics import VideoAnalytics  # noqa: F401
from backend.models.video_job import JobStatus, VideoJob  # noqa: F401

__all__ = [
    "AppSetting",
    "VideoJob",
    "JobStatus",
    "PlatformAccount",
    "Channel",
    "DriveConnection",
    "PublishSession",
    "ReadyVideo",
    "ScheduleConfig",
    "ThemeQueue",
    "VideoAnalytics",
]
