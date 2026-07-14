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


# Hard ceiling on a single upload attempt. googleapiclient/httplib2 has NO default
# socket timeout, so a stalled TCP connect/read (dead peer, blackholed route — the
# kind of network hiccup Railway sees) can block the worker thread FOREVER. Since
# that thread runs under `_publish_sem` (dispatch.py, cap=2), two hung uploads
# silently exhaust the entire publish pool and every future job queues forever
# with no error. asyncio.wait_for can't cancel the underlying thread, but it DOES
# free the semaphore/job immediately so the pipeline keeps moving.
#
# Deliberately short (was 1800s/30min): a DEAD connection (DNS resolution
# hanging, a blackholed route) produces ZERO cpu/network activity for the
# ENTIRE wait — confirmed via Railway metrics showing a flat 0.0 vCPU during
# a stuck job. A wait this long just delays the inevitable failure/retry by
# half an hour per attempt while a job silently occupies a publish slot,
# masking the problem instead of surfacing it. 300s matches the httplib2
# socket timeout in uploaders/youtube.py -- generous for a real (if slow)
# upload of the short clips this pipeline actually handles, tight enough
# that a genuinely dead attempt fails fast and frees the slot for retry.
_UPLOAD_TIMEOUT_S = 300


async def _with_retry(fn, *args, label="upload", **kwargs) -> dict:
    import os

    fast = os.getenv("STUDIO_FAST_RETRY") == "1"
    last = {}
    for attempt in range(3):
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(fn, *args, **kwargs), timeout=_UPLOAD_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            result = {"ok": False, "status": "error",
                      "error": f"{label} timed out after {_UPLOAD_TIMEOUT_S}s"}
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
    logger.warning("CANARY_RUN_PUBLISH enter job=%s", job_id)
    db = SessionLocal()
    logger.warning("CANARY_RUN_PUBLISH got db session job=%s", job_id)
    job = None
    results: dict = {}
    try:
        job = db.get(VideoJob, job_id)
        logger.warning("CANARY_RUN_PUBLISH loaded job=%s status=%s", job_id, getattr(job, "status", None))
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
        platforms = ["youtube"] if job.target_platforms is None else job.target_platforms
        prior = job.publish_status or {}
        if isinstance(prior, dict):
            results = dict(prior)
            yt_prior = prior.get("youtube") or {}
            if yt_prior.get("ok") and yt_prior.get("video_id") and all(
                (prior.get(platform) or {}).get("ok") for platform in platforms
            ):
                if job.status != JobStatus.PUBLISHED:
                    job.status = JobStatus.PUBLISHED
                    db.commit()
                logger.info("Publicação ignorada (idempotente): job %s já tem vídeo %s.",
                            job_id, yt_prior.get("video_id"))
                return {"status": JobStatus.PUBLISHED.value, "results": prior,
                        "note": "já publicado — upload duplicado evitado"}

        svc = AccountProfileService(db)
        seo = job.seo_metadata or {}
        job.status = JobStatus.PUBLISHING
        db.commit()
        _emit(job_id, status="publishing")

        # Runs a synchronous Google Drive download (googleapiclient/httplib2, no
        # default socket timeout) DIRECTLY on the shared render/publish worker
        # loop (dispatch.py) if called bare. A stalled connection there doesn't
        # just occupy one semaphore slot like a hung YouTube upload does — it
        # FREEZES THE ENTIRE EVENT LOOP, since nothing yields control back until
        # this call returns. to_thread moves it off-loop; wait_for bounds it so
        # a genuinely stalled download can't wedge the pipeline forever.
        # Uses its OWN DB session (job_id only, not this coroutine's `db`/`job`)
        # -- see _ensure_ready_video_local's docstring for why sharing one
        # across threads is unsafe. Refresh afterwards to pick up its commit.
        await asyncio.wait_for(
            asyncio.to_thread(_ensure_ready_video_local, job_id), timeout=_UPLOAD_TIMEOUT_S
        )
        db.refresh(job)
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
            if isinstance(results.get(platform), dict) and results[platform].get("ok"):
                _emit(job_id, platform=platform, status=results[platform].get("status", "already_published"))
                continue
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
                    results["youtube"] = await publish_youtube(
                        job, seo, creds, publish_at, shorts, privacy,
                        content_language=getattr(acct, "content_language", None) or "pt-BR")
                elif platform == "tiktok":
                    # Same durable "uploading" marker as YouTube above — without it,
                    # orphan recovery (main._apply_orphan_transition) has no way to
                    # know a TikTok upload was in flight when the process died, and
                    # could blindly re-publish a video that's already on TikTok.
                    job.publish_status = {**results, "tiktok": {
                        "status": "uploading",
                        "started_at": datetime.utcnow().isoformat() + "Z",
                        "account_id": acct.id,
                    }}
                    db.commit()
                    results["tiktok"] = await self_publish_tiktok(seo, creds, shorts, privacy)
                elif platform == "instagram":
                    job.publish_status = {**results, "instagram": {
                        "status": "uploading",
                        "started_at": datetime.utcnow().isoformat() + "Z",
                        "account_id": acct.id,
                    }}
                    db.commit()
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
        _finish_ready_video_if_published(db, job, results)
        # Surface a human, actionable reason on the job row instead of a cryptic
        # "[Errno 2] No such file..." so the user knows to REGENERATE, not retry.
        if job.status == JobStatus.AWAITING_QUOTA:
            job.error_message = (
                "Quota diaria do YouTube atingida. O video ficou aguardando "
                "o reset da quota e sera retomado automaticamente."
            )
        elif job.status == JobStatus.ERROR:
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
        else:
            failed = [p for p, r in results.items() if not (r or {}).get("ok")]
            job.error_message = (
                f"Publicado parcialmente; falha em: {', '.join(failed)}."[:500]
                if failed else None
            )
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
                # A pre-upload step (e.g. _ensure_ready_video_local's Drive download)
                # can raise our own wall-clock-deadline TimeoutError directly, bypassing
                # publish_youtube's auth_error classification entirely. Recognize
                # it here too so the message matches _NO_AUTO_RETRY_MARKERS (park until
                # reconnect) instead of getting endlessly auto-resurrected with a
                # cryptic message that never tells the user to reconnect the channel.
                if "travado" in str(exc).lower():
                    job.error_message = _AUTH_BLOCKED
                else:
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


async def publish_youtube(job, seo, creds, publish_at, shorts, privacy="private",
                          content_language="pt-BR") -> dict:
    if not (job.main_video_path and os.path.exists(job.main_video_path)):
        return {"ok": False, "platform": "youtube", "status": "file_missing", "error": _FILE_GONE}
    y = seo.get("youtube", {})
    _lang = content_language or "pt-BR"  # BCP-47; stamped as metadata + audio language
    # Native Short (vertical <60s): stamp #Shorts on the upload title so YouTube's
    # Shorts classifier files it in the Shorts feed. The description already carries
    # #Shorts (seo_agent adds it for short format). Derived shorts handled below.
    main_title = y.get("title", job.title)
    if getattr(job, "video_format", "long") == "short" and "#short" not in (main_title or "").lower():
        main_title = (main_title + " #Shorts")[:100]
    main = await _with_retry(
        yt.upload_video, job.main_video_path, main_title,
        y.get("description", ""), y.get("tags", []), creds,
        category_id=y.get("category_id", "22"), privacy=privacy, publish_at=publish_at,
        thumbnail_path=job.thumbnail_path,
        default_language=_lang, default_audio_language=_lang, label="yt-main",
    )
    short_results = []
    if main.get("ok"):
        # Upload the SRT as a real caption track (search transcript + CC + free auto-
        # translation). The caption_agent writes captions.srt next to the rendered
        # video; reconstruct that path. Strictly best-effort — never affects the publish.
        try:
            vid = main.get("video_id")
            srt = os.path.join(os.path.dirname(job.main_video_path), "captions.srt")
            if vid and os.path.exists(srt):
                cap = await asyncio.to_thread(yt.upload_captions, creds, vid, srt, _lang)
                if not cap.get("ok"):
                    logger.info("caption track not attached for job %s: %s",
                                getattr(job, "id", "?"), cap.get("error") or cap.get("status"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("caption upload skipped for job %s: %s", getattr(job, "id", "?"), exc)
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
        # Derived shorts ride the long video's SEO; force the Shorts feed signal into
        # BOTH title and the first description line (#Shorts above the title), since the
        # long video's description has no #Shorts of its own.
        short_desc = y.get("description", "")
        if "#short" not in short_desc.lower():
            short_desc = "#Shorts\n" + short_desc
        for sp in shorts:
            if sp == job.main_video_path:
                continue
            short_results.append(await _with_retry(
                yt.upload_video, sp, (y.get("title", job.title) + " #Shorts")[:100],
                short_desc, y.get("tags", []), creds,
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
    """Pick a short by its format number suffix (video_*_short_<n>.mp4), else None."""
    for s in shorts:
        if f"_short_{prefer}." in s:
            return s
    return None


def _ready_video_id(job) -> int | None:
    ctx = job.video_context or {}
    try:
        return int(ctx.get("ready_video_id") or 0) or None
    except (TypeError, ValueError):
        return None


def _ensure_ready_video_local(job_id: int) -> None:
    """Runs off-thread (see run_publish) — opens its OWN DB session instead of
    sharing the caller's. SQLAlchemy Sessions are not thread-safe: reusing one
    across the event-loop thread and this worker thread is undefined behavior
    and can silently deadlock the underlying DBAPI connection with ZERO cpu
    usage — indistinguishable from a hung network call, and the reason this
    session spent hours chasing what looked like a dead socket. A dedicated
    session per thread removes that hazard entirely."""
    from backend.agents.drive_library import DriveLibraryService
    from backend.models import ReadyVideo

    db = SessionLocal()
    try:
        job = db.get(VideoJob, job_id)
        if not job:
            return
        ready_id = _ready_video_id(job)
        if not ready_id:
            return
        if job.main_video_path and os.path.exists(job.main_video_path):
            return
        ready = db.get(ReadyVideo, ready_id)
        if not ready:
            return
        path = DriveLibraryService(db).download_for_job(ready, job.id)
        job.main_video_path = path
        if (job.video_format or "long") == "short":
            job.shorts_paths = [path]
        db.commit()
    finally:
        db.close()


def _finish_ready_video_if_published(db, job, results: dict) -> None:
    if not any((r or {}).get("ok") for r in results.values()):
        return
    ready_id = _ready_video_id(job)
    if not ready_id:
        return
    from backend.agents.drive_library import DriveLibraryService

    DriveLibraryService(db).mark_used(ready_id, job.id)


def _overall_status(results: dict) -> JobStatus:
    statuses = [r.get("status") for r in results.values()]
    if any(r.get("ok") for r in results.values()):
        return JobStatus.PUBLISHED
    if "tiktok_pending_approval" in statuses:
        return JobStatus.TIKTOK_PENDING_APPROVAL
    if "quota_exceeded" in statuses:
        return JobStatus.AWAITING_QUOTA
    return JobStatus.ERROR


if __name__ == "__main__":
    import sys

    jid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(asyncio.run(run_publish(jid)))
