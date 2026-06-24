"""
PublisherAgent — multi-platform upload of an APPROVED job (never before approval).

Per platform: pick the active account, check quota + credentials, upload with
3x retry/backoff, record results in job.publish_status. YouTube gets the main
video + each Short; TikTok/Instagram get a vertical Short.

Honours: YouTube quota (no extra projects), TikTok approval hold, IG rate limit.
After a successful YouTube publish, triggers cross-platform mirroring if linked.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

from backend.config import settings
from backend.database import SessionLocal
from backend.events import publish_event
from backend.models import JobStatus, VideoJob
from backend.agents.account_profile import AccountProfileService
from backend.uploaders import youtube as yt
from backend.uploaders import tiktok as tk
from backend.uploaders import instagram as ig

logger = logging.getLogger("studio.publisher")
RETRY_BACKOFFS = [60, 300, 900]  # 1min / 5min / 15min


def _emit(job_id, **fields):
    publish_event({"type": "publish_update", "job_id": job_id, **fields})


async def _with_retry(fn, *args, label="upload", **kwargs) -> dict:
    import os

    fast = os.getenv("STUDIO_FAST_RETRY") == "1"
    last = {}
    for attempt in range(3):
        result = await asyncio.to_thread(fn, *args, **kwargs)
        if result.get("ok"):
            return result
        last = result
        # Don't retry terminal states (approval/quota/auth/config/missing media).
        # Retrying these never succeeds and — critically — sleeping through the
        # backoff while holding a worker slot is what jammed the pipeline.
        if result.get("status") in {"tiktok_pending_approval", "quota_exceeded",
                                     "auth_error", "not_configured", "library_missing",
                                     "file_missing", "no_short"}:
            return result
        if attempt < 2:
            await asyncio.sleep(2 if fast else RETRY_BACKOFFS[attempt])
    return last


async def run_publish(job_id: int) -> dict:
    db = SessionLocal()
    job = None
    results: dict = {}
    try:
        job = db.get(VideoJob, job_id)
        if not job:
            return {"error": "job not found"}
        if job.status not in (JobStatus.APPROVED, JobStatus.PUBLISHING):
            return {"error": f"job não aprovado (status={job.status})"}

        # Idempotency guard — NEVER upload the same job to YouTube twice.
        # In schedule mode the orchestrator dispatches the publish itself; if a
        # restart, retry, or a stray publish_due tick re-enters here, an already
        # uploaded video (publish_status.youtube.ok + video_id) must NOT be
        # re-sent — that is exactly what produced the duplicate videos on the
        # channel. Mark it PUBLISHED and bail.
        prior = job.publish_status or {}
        if isinstance(prior, dict):
            yt_prior = prior.get("youtube") or {}
            if yt_prior.get("ok") and yt_prior.get("video_id"):
                if job.status != JobStatus.PUBLISHED:
                    job.status = JobStatus.PUBLISHED
                    db.commit()
                logger.info("Publicação ignorada (idempotente): job %s já tem vídeo %s.",
                            job_id, yt_prior.get("video_id"))
                return {"status": JobStatus.PUBLISHED.value, "results": prior,
                        "note": "já publicado — upload duplicado evitado"}

        svc = AccountProfileService(db)
        seo = job.seo_metadata or {}
        platforms = job.target_platforms or ["youtube"]
        job.status = JobStatus.PUBLISHING
        db.commit()
        _emit(job_id, status="publishing")

        shorts = job.shorts_paths or []
        # YouTube's publishAt must be in the FUTURE. In the slot-gated/auto-publish
        # model the slot has already arrived (scheduled_at <= now), so a past value
        # would make the API error or ignore the schedule — publish immediately
        # instead. Only defer when the slot is genuinely still ahead.
        from datetime import datetime as _dt, timezone as _tz
        publish_at = None
        sa = job.scheduled_at
        if sa:
            # Normalize to naive UTC — SQLite stores naive, but an in-memory
            # object set from a Pydantic payload may carry tzinfo.
            if sa.tzinfo is not None:
                sa = sa.astimezone(_tz.utc).replace(tzinfo=None)
            if sa > _dt.utcnow():
                publish_at = sa.isoformat() + "Z"
        # Privacy chosen for the job (default 'private' = nothing goes public
        # until the user explicitly opts in). STUDIO_TEST_MODE forces private.
        privacy = _resolve_privacy(job)

        # The job may be pinned to a specific account (job.account_id). Resolve it
        # up front so we publish on the RIGHT channel instead of the highest-quota
        # one — critical when the user has 2+ YouTube channels connected.
        pinned = svc.get(job.account_id) if job.account_id else None

        for platform in platforms:
            # Isolate each platform: a crash uploading to one must never abort the
            # others (or leave the whole multi-platform job stuck/ERROR).
            try:
                acct = _resolve_account(svc, platform, pinned)
                if not acct:
                    results[platform] = {"ok": False, "status": "no_account",
                                         "error": f"Sem conta ativa de {platform}."}
                    _emit(job_id, platform=platform, status="no_account")
                    continue
                # Guard: never hand an account without connected credentials to the
                # uploader (otherwise YouTube fails with a cryptic RefreshError).
                if not svc.has_valid_credentials(acct):
                    results[platform] = {
                        "ok": False, "status": "auth_error",
                        "error": "Conta sem credenciais conectadas. Conecte a conta em Contas.",
                    }
                    _emit(job_id, platform=platform, status="auth_error")
                    continue
                if not svc.can_upload(acct.id):
                    svc.pause(acct.id, "quota_exceeded")
                    results[platform] = {"ok": False, "status": "quota_exceeded",
                                         "error": f"Quota diária de {platform} atingida."}
                    _emit(job_id, platform=platform, status="quota_exceeded")
                    continue
                creds = svc.get_credentials(acct.id)

                if platform == "youtube":
                    # Durable "upload in progress" marker committed BEFORE the upload.
                    # If the process restarts mid-upload (redeploy / OOM), the video may
                    # already be on the channel even though we never recorded its id —
                    # orphan recovery reads this and HOLDS the job instead of blindly
                    # republishing it, which is exactly how the duplicate videos happened.
                    job.publish_status = {**results, "youtube": {
                        "status": "uploading",
                        "started_at": datetime.utcnow().isoformat() + "Z",
                        "account_id": acct.id,
                    }}
                    db.commit()
                    results["youtube"] = await self_publish_youtube(
                        job, seo, creds, publish_at, shorts, privacy,
                        content_language=getattr(acct, "content_language", None) or "pt-BR")
                elif platform == "tiktok":
                    results["tiktok"] = await self_publish_tiktok(seo, creds, shorts, privacy)
                elif platform == "instagram":
                    results["instagram"] = await self_publish_instagram(seo, creds, shorts)
                else:
                    results[platform] = {"ok": False, "status": "unsupported"}

                if results.get(platform, {}).get("ok"):
                    svc.record_upload(acct.id)
                _emit(job_id, platform=platform, status=results[platform].get("status"))
            except Exception as exc:  # noqa: BLE001 — per-platform isolation
                logger.warning("Publicação em %s falhou para job %s: %s", platform, job_id, exc)
                results[platform] = {"ok": False, "status": "error", "error": str(exc)[:300]}
                _emit(job_id, platform=platform, status="error")

        job.publish_status = results
        job.status = _overall_status(results)
        # Surface a human, actionable reason on the job row instead of a cryptic
        # "[Errno 2] No such file..." so the user knows to REGENERATE, not retry.
        if job.status == JobStatus.ERROR:
            if any((r or {}).get("status") == "file_missing" for r in results.values()):
                job.error_message = _FILE_GONE
            elif any((r or {}).get("status") == "auth_error" for r in results.values()):
                # Dead token: don't make the user click Retry (it can't work until the
                # channel is reconnected). Tell them what to do — the reconnect itself
                # auto-republishes this job (see dispatch.resume_account_blocked_jobs).
                job.error_message = _AUTH_BLOCKED
            else:
                first_err = next((r.get("error") for r in results.values()
                                  if isinstance(r, dict) and r.get("error")), None)
                job.error_message = (first_err or "Falha na publicação.")[:500]
        db.commit()
        _emit(job_id, status=job.status.value, results=results)

        # Cross-platform mirror after a successful YouTube publish.
        if results.get("youtube", {}).get("ok"):
            try:
                from backend.agents.cross_platform_linker import mirror_after_publish

                mirror_after_publish(db, job)
            except Exception as exc:  # noqa: BLE001
                logger.debug("mirror skipped: %s", exc)

        return {"status": job.status.value, "results": results}
    except Exception as exc:  # noqa: BLE001
        # Never leave the job stuck in PUBLISHING — always land a terminal state.
        logger.exception("run_publish failed for job %s", job_id)
        if job is not None:
            try:
                job.publish_status = results or {}
                job.status = JobStatus.ERROR
                job.error_message = f"Falha na publicação: {exc}"[:500]
                db.commit()
                _emit(job_id, status=JobStatus.ERROR.value, error=str(exc))
            except Exception:  # noqa: BLE001
                logger.exception("could not persist ERROR status for job %s", job_id)
        return {"status": JobStatus.ERROR.value, "error": str(exc), "results": results}
    finally:
        db.close()


_FILE_GONE = ("Os arquivos do vídeo foram perdidos (o servidor reiniciou). "
              "Gere o vídeo novamente (↻) para poder publicar.")

# Keep the "invalid_grant" token in this string: scheduler._NO_AUTO_RETRY_MARKERS and
# dispatch.resume_account_blocked_jobs both match on it to (a) NOT waste LLM retrying
# while blocked and (b) auto-republish the moment the channel is reconnected.
_AUTH_BLOCKED = ("Login do YouTube expirou (invalid_grant) — reconecte o canal em "
                 "Plataformas e o vídeo publica sozinho. Não precisa clicar Retry.")


async def self_publish_youtube(job, seo, creds, publish_at, shorts, privacy="private",
                               content_language="pt-BR") -> dict:
    if not (job.main_video_path and os.path.exists(job.main_video_path)):
        return {"ok": False, "platform": "youtube", "status": "file_missing", "error": _FILE_GONE}
    y = seo.get("youtube", {})
    _lang = content_language or "pt-BR"  # BCP-47; stamped as metadata + audio language
    main = await _with_retry(
        yt.upload_video, job.main_video_path, y.get("title", job.title),
        y.get("description", ""), y.get("tags", []), creds,
        category_id=y.get("category_id", "22"), privacy=privacy, publish_at=publish_at,
        thumbnail_path=job.thumbnail_path,
        default_language=_lang, default_audio_language=_lang, label="yt-main",
    )
    short_results = []
    if main.get("ok"):
        # Group the upload into its series/topic playlist for session-time. The
        # playlist_target is produced by the SEO agent and was never consumed.
        # Strictly best-effort: a playlist failure must NOT affect the publish.
        try:
            pl_title = ((seo.get("feed") or {}).get("playlist_target") or "").strip()
            vid = main.get("video_id")
            if pl_title and vid:
                pid = await asyncio.to_thread(yt.ensure_playlist, creds, pl_title)
                if pid:
                    await asyncio.to_thread(yt.add_to_playlist, creds, pid, vid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("playlist grouping failed for job %s: %s", getattr(job, "id", "?"), exc)
        # Localized title/description for free international reach (opt-in via
        # LOCALIZE_LANGUAGES). Best-effort read-modify-write; never affects the publish.
        try:
            locs = ((seo.get("youtube") or {}).get("localizations")) or {}
            vid = main.get("video_id")
            if locs and vid:
                await asyncio.to_thread(yt.set_localizations, creds, vid,
                                        settings.default_language, locs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("localization apply failed for job %s: %s", getattr(job, "id", "?"), exc)
        for sp in shorts:
            if sp == job.main_video_path:
                continue
            short_results.append(await _with_retry(
                yt.upload_video, sp, (y.get("title", job.title) + " #shorts")[:100],
                y.get("description", ""), y.get("tags", []), creds,
                category_id=y.get("category_id", "22"), privacy=privacy,
                default_language=_lang, default_audio_language=_lang, label="yt-short",
            ))
    return {**main, "shorts": short_results}


async def self_publish_tiktok(seo, creds, shorts, privacy="private") -> dict:
    caption = seo.get("tiktok", {}).get("caption", "")
    target = _pick_short(shorts, prefer=4) or _pick_short(shorts, prefer=2)
    if not target:
        return {"ok": False, "platform": "tiktok", "status": "no_short", "error": "Sem Short para TikTok."}
    if not os.path.exists(target):
        return {"ok": False, "platform": "tiktok", "status": "file_missing", "error": _FILE_GONE}
    return await _with_retry(tk.upload_video, target, caption, creds, privacy=privacy, label="tiktok")


async def self_publish_instagram(seo, creds, shorts) -> dict:
    ig_meta = seo.get("instagram", {})
    caption = ig_meta.get("caption", "")
    hashtags = " ".join(ig_meta.get("hashtags", []))
    target = _pick_short(shorts, prefer=3) or _pick_short(shorts, prefer=2)
    if not target:
        return {"ok": False, "platform": "instagram", "status": "no_short", "error": "Sem Short para IG."}
    if not os.path.exists(target):
        return {"ok": False, "platform": "instagram", "status": "file_missing", "error": _FILE_GONE}
    return await _with_retry(ig.upload_reel, target, f"{caption}\n\n{hashtags}".strip(), creds, label="ig")


def _resolve_account(svc, platform, pinned):
    """Resolve the account to publish on for a given platform.

    If the job is pinned to a specific account (job.account_id) and that account
    is for THIS platform and has valid connected credentials, use it — this makes
    us publish on the channel the user chose instead of whichever active account
    happens to have the most quota. Otherwise fall back to get_active_account so
    extra target platforms (or an empty/invalid pin) still resolve sensibly.
    """
    if pinned and pinned.platform == platform and svc.has_valid_credentials(pinned):
        return pinned
    return svc.get_active_account(platform)


def _resolve_privacy(job) -> str:
    """Privacy for this publish.

    Per-job `job.privacy` wins if set; otherwise the account-wide DEFAULT_PRIVACY
    (settings.default_privacy) decides — set it to 'public' to actually reach the
    audience. STUDIO_TEST_MODE=1 still hard-forces 'private' so test runs never
    leak public uploads.
    """
    import os

    from backend.config import settings

    if os.getenv("STUDIO_TEST_MODE") == "1":
        return "private"
    choice = getattr(job, "privacy", None)
    if choice in {"public", "unlisted", "private"}:
        return choice
    default = (settings.default_privacy or "private").lower()
    return default if default in {"public", "unlisted", "private"} else "private"


def _pick_short(shorts: list[str], prefer: int) -> str | None:
    """Pick a short by its format number suffix (video_*_short_<n>.mp4), else first."""
    for s in shorts:
        if f"_short_{prefer}." in s:
            return s
    return shorts[0] if shorts else None


def _overall_status(results: dict) -> JobStatus:
    statuses = [r.get("status") for r in results.values()]
    if any(r.get("ok") for r in results.values()):
        return JobStatus.PUBLISHED
    if "tiktok_pending_approval" in statuses:
        return JobStatus.TIKTOK_PENDING_APPROVAL
    return JobStatus.ERROR


if __name__ == "__main__":
    import sys

    jid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(asyncio.run(run_publish(jid)))
