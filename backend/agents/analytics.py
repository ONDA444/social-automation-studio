"""
AnalyticsAgent — collect post-publish metrics for learning + the Analytics page.

Snapshots at 2h / 24h / 7d after publish. Reads real numbers from platform APIs
when credentials exist; otherwise no-ops gracefully. Results feed
ContentCalendarAgent (best hours) and the thumbnail A/B comparison.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
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
            # Pull watch-time on the fixed 2h/24h/7d collect (YT Analytics latency is
            # 1-3 days, so it's worthless at 2h but populated by 24h/7d).
            metrics = self._fetch(platform, res["video_id"], creds, with_analytics=True)
            if metrics is None:
                continue
            # Upsert on (job_id, platform, snapshot_type) so repeated calls (e.g. the
            # manual "collect now" button in routers/analytics.py) don't create
            # duplicate rows for the same fixed snapshot.
            row = self.db.execute(
                select(VideoAnalytics).where(
                    VideoAnalytics.job_id == job_id,
                    VideoAnalytics.platform == platform,
                    VideoAnalytics.snapshot_type == snapshot_type,
                )
            ).scalars().first()
            if row is None:
                row = VideoAnalytics(
                    job_id=job_id, platform=platform, platform_video_id=res["video_id"],
                    snapshot_type=snapshot_type,
                    thumbnail_variant=self._thumbnail_variant(job.thumbnail_path),
                )
                self.db.add(row)
            for k, v in metrics.items():
                setattr(row, k, v)
            row.platform_video_id = res["video_id"]
            out.append({"platform": platform, **metrics})
        self.db.commit()
        return out

    def refresh_live(self, job_id: int) -> list[dict]:
        """Upsert a continuously-refreshed 'live' snapshot with the CURRENT platform
        numbers.

        The fixed 2h/24h/7d snapshots are taken once and then frozen — so a video
        published days ago would forever show its 7-day count while the platform kept
        growing (e.g. 30 here vs 923 on YouTube). This row is instead OVERWRITTEN every
        cycle, so it always holds what the platform shows right now. Because it carries
        the newest collected_at, both the Analytics page and the learning loop (which
        already pick the latest snapshot per platform) use these live numbers."""
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
            row = self.db.execute(
                select(VideoAnalytics).where(
                    VideoAnalytics.job_id == job_id,
                    VideoAnalytics.platform == platform,
                    VideoAnalytics.snapshot_type == "live",
                )
            ).scalars().first()
            if row is None:
                row = VideoAnalytics(
                    job_id=job_id, platform=platform, platform_video_id=res["video_id"],
                    snapshot_type="live",
                    thumbnail_variant=self._thumbnail_variant(job.thumbnail_path),
                )
                self.db.add(row)
            for k, v in metrics.items():
                setattr(row, k, v)
            row.platform_video_id = res["video_id"]
            row.collected_at = datetime.utcnow()  # bump so this stays the "latest"
            out.append({"platform": platform, **metrics})
        self.db.commit()
        return out

    @staticmethod
    def _thumbnail_variant(thumbnail_path: str | None) -> str:
        """Which A/B thumbnail variant was actually published, read back from the
        filename VisualsAgent uses (thumb_A.png / thumb_B.png -- see visuals.py).
        Defaults to "A" when unknown so old jobs without a stored path still get a
        value."""
        if thumbnail_path and "thumb_B" in thumbnail_path:
            return "B"
        return "A"

    def _fetch(self, platform: str, video_id: str, creds: dict,
               with_analytics: bool = False) -> dict | None:
        if platform == "youtube":
            return self._youtube(video_id, creds, with_analytics=with_analytics)
        if platform == "instagram":
            return self._instagram(video_id, creds)
        if platform == "tiktok":
            return self._tiktok(video_id, creds)
        return None

    def _youtube(self, video_id: str, creds: dict, with_analytics: bool = False) -> dict | None:
        if not creds:
            return None
        try:
            from backend.uploaders.youtube import _service

            yt = _service(creds)
            resp = yt.videos().list(part="statistics", id=video_id).execute()
            stats = (resp.get("items") or [{}])[0].get("statistics", {})
            out = {
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "raw": stats,
            }
            if with_analytics:
                wt = self._youtube_watchtime(video_id, creds)
                if wt:
                    out.update(wt)
            return out
        except Exception as exc:  # noqa: BLE001
            logger.debug("YT analytics failed: %s", exc)
            return None

    @staticmethod
    def _youtube_watchtime(video_id: str, creds: dict) -> dict | None:
        """Watch-time via the YouTube Analytics API (scope yt-analytics.readonly, already
        granted on youtube.py:22 but never used). This is the metric the learning loop
        actually needs (the 4000h YPP threshold is watch-HOURS, not views). Best-effort;
        returns None so the caller still keeps the basic stats."""
        try:
            from datetime import timedelta

            from googleapiclient.discovery import build

            from backend.uploaders.youtube import _credentials

            ya = build("youtubeAnalytics", "v2",
                       credentials=_credentials(creds), cache_discovery=False)
            end = datetime.utcnow().date()
            start = end - timedelta(days=400)
            resp = ya.reports().query(
                ids="channel==MINE",
                startDate=start.isoformat(), endDate=end.isoformat(),
                metrics="estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained",
                filters=f"video=={video_id}",
            ).execute()
            rows = resp.get("rows") or []
            if not rows:
                return None
            cols = [h.get("name") for h in resp.get("columnHeaders", [])]
            vals = dict(zip(cols, rows[0]))
            pct = float(vals.get("averageViewPercentage", 0) or 0)
            out = {
                "watch_minutes": int(vals.get("estimatedMinutesWatched", 0) or 0),
                "avg_view_seconds": float(vals.get("averageViewDuration", 0) or 0),
                "avg_view_pct": pct,
                "subscribers_gained": int(vals.get("subscribersGained", 0) or 0),
                "retention_avg": pct / 100.0,
                "completion_rate": pct / 100.0,
            }
            try:
                ctr_resp = ya.reports().query(
                    ids="channel==MINE",
                    startDate=start.isoformat(), endDate=end.isoformat(),
                    metrics="impressions,impressionsClickThroughRate",
                    filters=f"video=={video_id}",
                ).execute()
                ctr_rows = ctr_resp.get("rows") or []
                if ctr_rows:
                    ctr_cols = [h.get("name") for h in ctr_resp.get("columnHeaders", [])]
                    ctr_vals = dict(zip(ctr_cols, ctr_rows[0]))
                    out["impressions"] = int(ctr_vals.get("impressions", 0) or 0)
                    out["ctr"] = float(ctr_vals.get("impressionsClickThroughRate", 0) or 0)
            except Exception as exc:  # noqa: BLE001
                logger.debug("YT CTR fetch failed: %s", exc)
            return out
        except Exception as exc:  # noqa: BLE001
            logger.debug("YT watch-time failed: %s", exc)
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
