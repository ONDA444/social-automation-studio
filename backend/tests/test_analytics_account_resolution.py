from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agents.account_profile import AccountProfileService
from backend.agents.analytics import AnalyticsAgent
from backend.crypto import encrypt_credentials
from backend.database import Base
from backend.models import JobStatus, PlatformAccount, VideoJob


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


class AccountForVideoTests(unittest.TestCase):
    """Regression test: both collectors used to resolve credentials via
    get_active_account(platform), which picks whichever connected account has
    the MOST quota remaining — in production that was one of two channels
    with ZERO published videos. Since the YouTube API call is scoped
    "channel==MINE", querying with the wrong channel's token for a video it
    doesn't own returns 0 rows silently, and the metric stays 0 forever with
    no error anywhere. Must resolve via job.account_id first."""

    def _setup(self, *, job_account_platform="youtube"):
        db = _make_session()
        owner = PlatformAccount(platform="youtube", display_name="Canal Dono",
                                 quota_limit=10000, quota_used_today=8000)
        richer_stranger = PlatformAccount(platform="youtube", display_name="Canal Rico",
                                           quota_limit=10000, quota_used_today=0)
        db.add_all([owner, richer_stranger])
        db.flush()
        job = VideoJob(
            title="V", mode="from_ready_video", content_type="film_recap_ai_images",
            video_format="long", account_id=owner.id, status=JobStatus.PUBLISHED,
            publish_status={"youtube": {"ok": True, "video_id": "abc123"}},
        )
        db.add(job)
        db.commit()
        return db, owner, richer_stranger, job

    def test_resolves_the_jobs_own_account_not_the_richest_one(self) -> None:
        db, owner, richer_stranger, job = self._setup()
        svc = AccountProfileService(db)

        resolved = AnalyticsAgent._account_for(svc, job, "youtube")

        self.assertEqual(resolved.id, owner.id)
        self.assertNotEqual(resolved.id, richer_stranger.id)

    def test_falls_back_to_active_account_for_cross_platform_mirror(self) -> None:
        db, owner, richer_stranger, job = self._setup()
        tiktok_acct = PlatformAccount(platform="tiktok", display_name="TikTok Mirror",
                                       quota_limit=1000, quota_used_today=0,
                                       credentials_encrypted=encrypt_credentials({"access_token": "x"}))
        db.add(tiktok_acct)
        db.commit()
        svc = AccountProfileService(db)

        # job.account_id points at a YOUTUBE account; asking for "tiktok"
        # credentials must fall back since the job's own account isn't tiktok.
        resolved = AnalyticsAgent._account_for(svc, job, "tiktok")

        self.assertEqual(resolved.id, tiktok_acct.id)


class RefreshLiveAgeGuardTests(unittest.TestCase):
    """refresh_live runs every 8 minutes; querying YT Analytics (1-3 day
    latency) on a video published minutes ago is guaranteed-empty and pure
    wasted quota. Must only request with_analytics once the video is >=24h
    old — and this is the ONLY snapshot the growth loop reads, so getting
    this wrong means CTR/retention never populate even with the API enabled."""

    def _setup(self, *, hours_old: float):
        db = _make_session()
        acct = PlatformAccount(platform="youtube", display_name="Canal")
        db.add(acct)
        db.flush()
        job = VideoJob(
            title="V", mode="from_ready_video", content_type="film_recap_ai_images",
            video_format="long", account_id=acct.id, status=JobStatus.PUBLISHED,
            publish_status={"youtube": {"ok": True, "video_id": "abc123"}},
            updated_at=datetime.utcnow() - timedelta(hours=hours_old),
        )
        db.add(job)
        db.commit()
        return db, job

    def test_young_video_does_not_request_analytics(self) -> None:
        db, job = self._setup(hours_old=1)
        agent = AnalyticsAgent(db)
        with patch.object(AnalyticsAgent, "_fetch", return_value={"views": 5}) as fake_fetch:
            agent.refresh_live(job.id)
        self.assertFalse(fake_fetch.call_args.kwargs.get("with_analytics", False))

    def test_video_older_than_24h_requests_analytics(self) -> None:
        db, job = self._setup(hours_old=30)
        agent = AnalyticsAgent(db)
        with patch.object(AnalyticsAgent, "_fetch", return_value={"views": 5}) as fake_fetch:
            agent.refresh_live(job.id)
        self.assertTrue(fake_fetch.call_args.kwargs.get("with_analytics", False))


class AnalyticsErrorSurfacingTests(unittest.TestCase):
    """A YT Analytics failure (e.g. the API disabled in the Cloud project ->
    403 SERVICE_DISABLED) used to be logger.debug'd and discarded — that
    silence is what let CTR/retention/watch-time sit at 0 across 379 rows
    with nothing anywhere pointing at why. The error must survive into the
    stored row so it's actually discoverable."""

    def test_watchtime_failure_returns_the_error_string(self) -> None:
        with patch("googleapiclient.discovery.build", side_effect=RuntimeError(
                "403 SERVICE_DISABLED: YouTube Analytics API has not been used")), \
             patch("backend.uploaders.youtube._credentials", return_value=object()):
            result, error = AnalyticsAgent._youtube_watchtime("vid123", {"token": "x"})

        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertIn("SERVICE_DISABLED", error)

    def test_youtube_fetch_surfaces_the_error_in_raw(self) -> None:
        agent = AnalyticsAgent(db=None)
        with patch("backend.uploaders.youtube._service") as fake_service, \
             patch.object(AnalyticsAgent, "_youtube_watchtime",
                           return_value=(None, "403 SERVICE_DISABLED")):
            fake_service.return_value.videos.return_value.list.return_value.execute.return_value = {
                "items": [{"statistics": {"viewCount": "10", "likeCount": "1", "commentCount": "0"}}]
            }
            out = agent._youtube("vid123", {"token": "x"}, with_analytics=True)

        self.assertEqual(out["views"], 10)
        self.assertIn("analytics_error", out["raw"])
        self.assertIn("SERVICE_DISABLED", out["raw"]["analytics_error"])


if __name__ == "__main__":
    unittest.main()
