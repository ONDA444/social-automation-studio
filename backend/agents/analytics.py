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

# YouTube Data/Analytics APIs accept up to 50 IDs per call at the SAME quota
# cost as a single ID (videos().list `id=`, reports().query `filters=video==`).
# refresh_live_batch() below chunks at this size instead of firing one call
# per video.
_YT_ID_CHUNK = 50


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
            acct = self._account_for(svc, job, platform)
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
        # YT Analytics latency is 1-3 days — querying it on a video <24h old just
        # burns quota for guaranteed-empty rows. This is also the ONLY snapshot
        # the growth loop reads (PerformanceInsights picks the latest collected_at
        # per platform, and this row's collected_at is bumped every cycle below),
        # so without with_analytics here, CTR/retention/watch-time stayed zeroed
        # forever even once the Analytics API itself was reachable.
        job_age_h = (
            (datetime.utcnow() - job.updated_at).total_seconds() / 3600
            if job.updated_at else 999
        )
        for platform, res in (job.publish_status or {}).items():
            if not res.get("ok") or not res.get("video_id"):
                continue
            acct = self._account_for(svc, job, platform)
            creds = svc.get_credentials(acct.id) if acct else {}
            metrics = self._fetch(platform, res["video_id"], creds, with_analytics=job_age_h >= 24)
            if metrics is None:
                continue
            self._upsert_live_row(job, platform, res["video_id"], metrics)
            out.append({"platform": platform, **metrics})
        self.db.commit()
        return out

    def refresh_live_batch(self, job_ids: list[int]) -> dict[int, list[dict]]:
        """Same as refresh_live(), but for many jobs in one shot: groups every
        job's YouTube video across the whole batch by the account whose
        credentials own it and fetches statistics/watch-time in chunks of up
        to 50 video IDs per call instead of one videos().list /
        reports().query call per video. The Data/Analytics APIs charge the
        SAME quota for a 50-ID chunk as for a single ID, so this is what
        actually cuts the HTTP-call volume behind the OOM burst
        _REFRESH_LIVE_BATCH_LIMIT (scheduler.py) caps the symptom of — not
        just the number of jobs processed per tick.

        Other platforms (Instagram/TikTok) aren't batchable the same way, so
        they still go through _fetch() per video, unchanged.
        """
        now = datetime.utcnow()
        jobs_by_id = {
            job.id: job
            for job in self.db.execute(select(VideoJob).where(VideoJob.id.in_(job_ids))).scalars()
        }
        svc = AccountProfileService(self.db)

        # account_id -> [(job, video_id, job_age_hours), ...]
        yt_groups: dict[int, list[tuple[VideoJob, str, float]]] = {}
        yt_creds: dict[int, dict] = {}
        other: list[tuple[VideoJob, str, str]] = []  # (job, platform, video_id)

        for job_id in job_ids:
            job = jobs_by_id.get(job_id)
            if not job or not job.publish_status:
                continue
            for platform, res in (job.publish_status or {}).items():
                if not res.get("ok") or not res.get("video_id"):
                    continue
                if platform != "youtube":
                    other.append((job, platform, res["video_id"]))
                    continue
                acct = self._account_for(svc, job, platform)
                if acct is None:
                    continue
                if acct.id not in yt_creds:
                    yt_creds[acct.id] = svc.get_credentials(acct.id)
                job_age_h = (
                    (now - job.updated_at).total_seconds() / 3600 if job.updated_at else 999
                )
                yt_groups.setdefault(acct.id, []).append((job, res["video_id"], job_age_h))

        results: dict[int, list[dict]] = {}

        for acct_id, entries in yt_groups.items():
            creds = yt_creds.get(acct_id)
            if not creds:
                continue
            video_ids = [video_id for _, video_id, _ in entries]
            analytics_ids = [video_id for _, video_id, age_h in entries if age_h >= 24]
            stats_by_id = self._youtube_batch_stats(video_ids, creds)
            wt_by_id, wt_error = self._youtube_watchtime_batch(analytics_ids, creds)
            for job, video_id, _age_h in entries:
                stats = stats_by_id.get(video_id)
                if stats is None:
                    continue
                metrics = dict(stats)
                wt = wt_by_id.get(video_id)
                if wt:
                    metrics.update(wt)
                if wt_error and video_id in analytics_ids:
                    metrics["raw"] = {**metrics.get("raw", {}), "analytics_error": wt_error}
                self._upsert_live_row(job, "youtube", video_id, metrics)
                results.setdefault(job.id, []).append({"platform": "youtube", **metrics})

        for job, platform, video_id in other:
            acct = self._account_for(svc, job, platform)
            creds = svc.get_credentials(acct.id) if acct else {}
            metrics = self._fetch(platform, video_id, creds, with_analytics=False)
            if metrics is None:
                continue
            self._upsert_live_row(job, platform, video_id, metrics)
            results.setdefault(job.id, []).append({"platform": platform, **metrics})

        self.db.commit()
        return results

    def _upsert_live_row(self, job: VideoJob, platform: str, video_id: str, metrics: dict) -> None:
        row = self.db.execute(
            select(VideoAnalytics).where(
                VideoAnalytics.job_id == job.id,
                VideoAnalytics.platform == platform,
                VideoAnalytics.snapshot_type == "live",
            )
        ).scalars().first()
        if row is None:
            row = VideoAnalytics(
                job_id=job.id, platform=platform, platform_video_id=video_id,
                snapshot_type="live",
                thumbnail_variant=self._thumbnail_variant(job.thumbnail_path),
            )
            self.db.add(row)
        for k, v in metrics.items():
            setattr(row, k, v)
        row.platform_video_id = video_id
        row.collected_at = datetime.utcnow()  # bump so this stays the "latest"

    @staticmethod
    def _account_for(svc: AccountProfileService, job: VideoJob, platform: str) -> PlatformAccount | None:
        """The credentials for the channel that ACTUALLY OWNS this video, not
        whichever account happens to have the most quota left. Both collectors
        used to call get_active_account(platform) — with 6 YouTube accounts
        connected, that resolves to whichever has the most quota remaining
        (account_profile.py), which in production was one of the 2 channels
        with ZERO published videos. Since the Analytics/Data API call is
        "channel==MINE" scoped to the token's own channel, querying with the
        WRONG channel's credentials for a video that channel doesn't own always
        returns 0 rows — silently, no error, just permanently empty metrics.
        Falls back to get_active_account only for a cross-platform mirror job
        whose own account isn't on this platform (e.g. a YouTube job mirrored
        to TikTok)."""
        acct = svc.get(job.account_id) if job.account_id else None
        if acct is not None and acct.platform == platform:
            return acct
        return svc.get_active_account(platform)

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
                wt, wt_error = self._youtube_watchtime(video_id, creds)
                if wt:
                    out.update(wt)
                if wt_error:
                    # This is the ONLY place a YT Analytics failure (e.g. the
                    # API being disabled in the Cloud project -> 403
                    # SERVICE_DISABLED) becomes visible anywhere. It used to be
                    # logger.debug'd and silently discarded, which is exactly
                    # what let CTR/retention/watch-time sit at 0 across every
                    # row for weeks with nothing in any log anyone was
                    # watching pointing at why.
                    out["raw"] = {**stats, "analytics_error": wt_error}
            return out
        except Exception as exc:  # noqa: BLE001
            logger.debug("YT analytics failed: %s", exc)
            return None

    @staticmethod
    def _youtube_watchtime(video_id: str, creds: dict) -> tuple[dict | None, str | None]:
        """Watch-time via the YouTube Analytics API (scope yt-analytics.readonly, already
        granted on youtube.py:22 but never used). This is the metric the learning loop
        actually needs (the 4000h YPP threshold is watch-HOURS, not views). Best-effort —
        the caller still keeps the basic view/like/comment stats on failure — but the
        error string (2nd tuple element) is always returned so it can be surfaced instead
        of vanishing into a debug log nobody reads."""
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
                return None, None
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
                logger.warning("YT CTR fetch failed for video %s: %s", video_id, exc)
            return out, None
        except Exception as exc:  # noqa: BLE001
            logger.warning("YT watch-time failed for video %s: %s", video_id, exc)
            return None, str(exc)

    def _youtube_batch_stats(self, video_ids: list[str], creds: dict) -> dict[str, dict]:
        """Batched counterpart to _youtube(): statistics for up to
        _YT_ID_CHUNK video IDs per videos().list call (same quota cost as one
        ID) instead of one call per video. Keyed by video ID so the caller can
        redistribute results to each job."""
        if not creds or not video_ids:
            return {}
        out: dict[str, dict] = {}
        try:
            from backend.uploaders.youtube import _service

            yt = _service(creds)
            for chunk in self._chunk(video_ids, _YT_ID_CHUNK):
                resp = yt.videos().list(part="statistics", id=",".join(chunk)).execute()
                stats_by_id = {item.get("id"): item.get("statistics", {}) for item in resp.get("items", [])}
                for video_id in chunk:
                    stats = stats_by_id.get(video_id, {})
                    out[video_id] = {
                        "views": int(stats.get("viewCount", 0)),
                        "likes": int(stats.get("likeCount", 0)),
                        "comments": int(stats.get("commentCount", 0)),
                        "raw": stats,
                    }
        except Exception as exc:  # noqa: BLE001
            logger.debug("YT batch analytics failed: %s", exc)
        return out

    @staticmethod
    def _youtube_watchtime_batch(video_ids: list[str], creds: dict) -> tuple[dict[str, dict], str | None]:
        """Batched counterpart to _youtube_watchtime(): one reports().query per
        chunk of up to _YT_ID_CHUNK IDs (dimensions=video splits the response
        into one row per video) instead of one query per video."""
        if not video_ids:
            return {}, None
        try:
            from datetime import timedelta

            from googleapiclient.discovery import build

            from backend.uploaders.youtube import _credentials

            ya = build("youtubeAnalytics", "v2",
                       credentials=_credentials(creds), cache_discovery=False)
            end = datetime.utcnow().date()
            start = end - timedelta(days=400)
            out: dict[str, dict] = {}
            for chunk in AnalyticsAgent._chunk(video_ids, _YT_ID_CHUNK):
                resp = ya.reports().query(
                    ids="channel==MINE",
                    startDate=start.isoformat(), endDate=end.isoformat(),
                    dimensions="video",
                    metrics="estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained",
                    filters=f"video=={','.join(chunk)}",
                ).execute()
                cols = [h.get("name") for h in resp.get("columnHeaders", [])]
                for row in resp.get("rows") or []:
                    vals = dict(zip(cols, row))
                    pct = float(vals.get("averageViewPercentage", 0) or 0)
                    out[vals.get("video")] = {
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
                        dimensions="video",
                        metrics="impressions,impressionsClickThroughRate",
                        filters=f"video=={','.join(chunk)}",
                    ).execute()
                    ctr_cols = [h.get("name") for h in ctr_resp.get("columnHeaders", [])]
                    for row in ctr_resp.get("rows") or []:
                        ctr_vals = dict(zip(ctr_cols, row))
                        video_id = ctr_vals.get("video")
                        if video_id in out:
                            out[video_id]["impressions"] = int(ctr_vals.get("impressions", 0) or 0)
                            out[video_id]["ctr"] = float(ctr_vals.get("impressionsClickThroughRate", 0) or 0)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("YT batch CTR fetch failed: %s", exc)
            return out, None
        except Exception as exc:  # noqa: BLE001
            logger.warning("YT batch watch-time failed: %s", exc)
            return {}, str(exc)

    @staticmethod
    def _chunk(items: list[str], size: int) -> list[list[str]]:
        return [items[i:i + size] for i in range(0, len(items), size)]

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
