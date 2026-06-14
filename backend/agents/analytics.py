"""
AnalyticsAgent — collect post-publish metrics for learning + the Analytics page.

Snapshots at 2h / 24h / 7d after publish. Reads real numbers from platform APIs
when credentials exist; otherwise no-ops gracefully. Results feed
ContentCalendarAgent (best hours) and the thumbnail A/B comparison.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from backend.agents.account_profile import AccountProfileService
from backend.models import PlatformAccount, VideoAnalytics, VideoJob

logger = logging.getLogger("studio.analytics")


class AnalyticsAgent:
    def __init__(self, db: Session) -> None:
        self.db = db

    def collect_for_job(self, job_id: int, snapshot_type: str = "2h") -> list[dict]:
        job = self.db.get(VideoJob, job_id)
        if not job or not job.publish_status:
            return []
        svc = AccountProfileService(self.db)
        out: list[dict] = []
        for platform, res in (job.publish_status or {}).items():
            if not res.get("ok") or not res.get("video_id"):
                continue
            acct = svc.get_active_account(platform)
            creds = svc.get_credentials(acct.id) if acct else {}
            metrics = self._fetch(platform, res["video_id"], creds)
            if metrics is None:
                continue
            row = VideoAnalytics(
                job_id=job_id, platform=platform, platform_video_id=res["video_id"],
                snapshot_type=snapshot_type, **metrics,
                thumbnail_variant="A",  # main upload uses variant A
            )
            self.db.add(row)
            out.append({"platform": platform, **metrics})
        self.db.commit()
        return out

    def _fetch(self, platform: str, video_id: str, creds: dict) -> dict | None:
        if platform == "youtube":
            return self._youtube(video_id, creds)
        if platform == "instagram":
            return self._instagram(video_id, creds)
        if platform == "tiktok":
            return self._tiktok(video_id, creds)
        return None

    def _youtube(self, video_id: str, creds: dict) -> dict | None:
        if not creds:
            return None
        try:
            from backend.uploaders.youtube import _service

            yt = _service(creds)
            resp = yt.videos().list(part="statistics", id=video_id).execute()
            stats = (resp.get("items") or [{}])[0].get("statistics", {})
            return {
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "raw": stats,
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("YT analytics failed: %s", exc)
            return None

    def _instagram(self, media_id: str, creds: dict) -> dict | None:
        token = creds.get("access_token")
        if not token:
            return None
        try:
            import httpx

            r = httpx.get(
                f"https://graph.facebook.com/v19.0/{media_id}/insights",
                params={"metric": "reach,likes,comments,saved,plays", "access_token": token},
                timeout=30,
            )
            data = {d["name"]: d["values"][0]["value"] for d in r.json().get("data", [])}
            return {"views": data.get("plays", 0), "likes": data.get("likes", 0),
                    "comments": data.get("comments", 0), "saves": data.get("saved", 0),
                    "impressions": data.get("reach", 0), "raw": data}
        except Exception as exc:  # noqa: BLE001
            logger.debug("IG analytics failed: %s", exc)
            return None

    def _tiktok(self, publish_id: str, creds: dict) -> dict | None:
        # TikTok video metrics require the Display API / Research scope.
        return None

    def thumbnail_ab_summary(self, account_id: int) -> dict:
        """Aggregate CTR by thumbnail variant for the account (A vs B)."""
        from sqlalchemy import func, select

        rows = self.db.execute(
            select(VideoAnalytics.thumbnail_variant, func.avg(VideoAnalytics.ctr),
                   func.sum(VideoAnalytics.views))
            .join(VideoJob, VideoJob.id == VideoAnalytics.job_id)
            .where(VideoJob.account_id == account_id)
            .group_by(VideoAnalytics.thumbnail_variant)
        ).all()
        return {v or "?": {"avg_ctr": float(c or 0), "views": int(s or 0)} for v, c, s in rows}
