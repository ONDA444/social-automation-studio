"""
TikTok Content Posting API (OFFICIAL). No Playwright / browser automation / bypass.

Requires an app approved for the Content Posting API at developers.tiktok.com
(3-5 business days). Until then, the publisher holds jobs as
'tiktok_pending_approval' rather than attempting any workaround.

Flow: OAuth (video.upload/video.publish) -> /post/publish/video/init/
(FILE_UPLOAD) -> PUT the file to the returned upload_url -> poll status.
"""
from __future__ import annotations

import logging
import os
import time

import httpx

from backend.config import settings

logger = logging.getLogger("studio.tiktok")

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
SCOPES = "user.info.basic,video.upload,video.publish"


def configured() -> bool:
    return bool(settings.tiktok_client_key and settings.tiktok_client_secret)


def _privacy_level(internal: str) -> str:
    """Map the studio's internal privacy ('public'/'unlisted'/'private') to a
    TikTok privacy_level enum. TikTok has no 'unlisted', so it collapses to
    private. While the app is unaudited (settings.tiktok_sandbox), TikTok only
    accepts SELF_ONLY — anything else is rejected — so we force it."""
    if settings.tiktok_sandbox:
        return "SELF_ONLY"
    return {
        "public": "PUBLIC_TO_EVERYONE",
        "unlisted": "SELF_ONLY",
        "private": "SELF_ONLY",
    }.get((internal or "private").lower(), "SELF_ONLY")


def build_auth_url(state: str = "") -> dict:
    if not configured():
        return {"ok": False, "error": "TIKTOK_CLIENT_KEY/SECRET não configurados",
                "status": "tiktok_pending_approval"}
    from urllib.parse import urlencode

    params = {
        "client_key": settings.tiktok_client_key,
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": settings.tiktok_redirect_uri,
        "state": state,
    }
    return {"ok": True, "auth_url": f"{AUTH_URL}?{urlencode(params)}"}


def exchange_code(code: str) -> dict:
    if not configured():
        return {"ok": False, "error": "TikTok não configurado", "status": "tiktok_pending_approval"}
    try:
        r = httpx.post(TOKEN_URL, data={
            "client_key": settings.tiktok_client_key,
            "client_secret": settings.tiktok_client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": settings.tiktok_redirect_uri,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
        r.raise_for_status()
        data = r.json()
        return {"ok": True, "credentials": {
            "access_token": data.get("access_token"),
            "refresh_token": data.get("refresh_token"),
            "open_id": data.get("open_id"),
            "expires_in": data.get("expires_in"),
            "scope": data.get("scope"),
        }}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "status": "error"}


# TikTok's publish_status enum values (per Content Posting API docs) mapped to
# the studio's internal job status.
_TERMINAL_STATUS = {
    "PUBLISH_COMPLETE": "published",
    "PUBLISH_FAILED": "error",
    "FAILED": "error",
}
_IN_PROGRESS_STATUS = {"PROCESSING_UPLOAD", "PROCESSING_DOWNLOAD", "PROCESSING"}


def _poll_publish_status(publish_id: str, headers: dict, max_wait: float = 120.0) -> dict:
    """Poll STATUS_URL until TikTok reports a terminal publish_status, or give up
    after ~max_wait seconds. Returns {'status': <internal status>, 'fail_reason': str|None}.

    Never assume 'published' just because the upload PUT succeeded — TikTok still
    has to download/process the file server-side and can fail at that stage."""
    delay = 2.0
    waited = 0.0
    last_raw = "unknown"
    while waited <= max_wait:
        try:
            r = httpx.post(STATUS_URL, json={"publish_id": publish_id}, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json().get("data", {})
            raw_status = data.get("status", "")
            last_raw = raw_status or last_raw
            if raw_status in _TERMINAL_STATUS:
                return {"status": _TERMINAL_STATUS[raw_status],
                        "fail_reason": data.get("fail_reason") if raw_status != "PUBLISH_COMPLETE" else None}
            if raw_status and raw_status not in _IN_PROGRESS_STATUS:
                # Unrecognized terminal-looking status: don't loop forever on it.
                return {"status": "processing", "fail_reason": None}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha ao consultar status de publicação TikTok (publish_id=%s): %s",
                            publish_id, exc)
        time.sleep(delay)
        waited += delay
        delay = min(delay * 1.5, 15.0)
    logger.warning("TikTok não confirmou publish_status a tempo (publish_id=%s, último status=%s)",
                    publish_id, last_raw)
    return {"status": "processing", "fail_reason": None}


def upload_video(video_path: str, caption: str, credentials: dict, privacy: str = "private") -> dict:
    if not configured():
        return {"ok": False, "platform": "tiktok", "status": "tiktok_pending_approval",
                "error": "App TikTok não aprovado / não configurado — publicação adiada."}
    token = credentials.get("access_token")
    if not token:
        return {"ok": False, "platform": "tiktok", "status": "auth_error",
                "error": "Sem access_token — reconecte a conta TikTok."}
    try:
        size = os.path.getsize(video_path)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        init_body = {
            "post_info": {
                "title": caption[:150],
                "privacy_level": _privacy_level(privacy),
                "disable_duet": False, "disable_comment": False, "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": size,        # single chunk
                "total_chunk_count": 1,
            },
        }
        r = httpx.post(INIT_URL, json=init_body, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json().get("data", {})
        publish_id = data.get("publish_id")
        upload_url = data.get("upload_url")
        if not upload_url:
            return {"ok": False, "platform": "tiktok", "status": "error",
                    "error": f"init sem upload_url: {r.text[:200]}"}

        with open(video_path, "rb") as f:
            # Stream the file handle instead of f.read() — avoids buffering the
            # whole video in memory for the duration of the PUT (httpx streams
            # file-like objects chunk-by-chunk).
            put = httpx.put(
                upload_url,
                content=f,
                headers={"Content-Range": f"bytes 0-{size - 1}/{size}",
                         "Content-Type": "video/mp4"},
                timeout=300,
            )
            put.raise_for_status()

        poll = _poll_publish_status(publish_id, headers)
        status = poll["status"]
        if status == "error":
            return {"ok": False, "platform": "tiktok", "video_id": publish_id,
                    "status": "error",
                    "error": f"TikTok falhou ao processar o vídeo: {poll.get('fail_reason') or 'motivo desconhecido'}"}
        note = ("Publicado com sucesso." if status == "published"
                else "Upload concluído; TikTok ainda está processando o vídeo.")
        return {"ok": True, "platform": "tiktok", "video_id": publish_id,
                "status": status, "note": note}
    except httpx.HTTPStatusError as exc:
        # Surface TikTok's REAL reason (it lives in the JSON body, not the generic
        # httpx message) so failures are diagnosable instead of a mystery "error".
        sc = exc.response.status_code
        try:
            body = exc.response.json()
            code = ((body.get("error") or {}).get("code") or "")
            detail = str(body)[:200]
        except Exception:  # noqa: BLE001
            code, detail = "", exc.response.text[:200]
        logger.warning("TikTok recusou publicação (HTTP %s, code=%s): %s", sc, code, detail)
        if sc == 401 or code == "access_token_invalid":
            return {"ok": False, "platform": "tiktok", "status": "auth_error",
                    "error": "Token TikTok inválido — reconecte a conta em Plataformas."}
        if sc == 403:
            # Unaudited app / scope not granted / account not allow-listed. Retrying
            # never fixes this — mark terminal (no-retry) and tell the user what to do.
            return {"ok": False, "platform": "tiktok", "status": "tiktok_pending_approval",
                    "error": ("TikTok recusou a publicação (403). O app ainda não foi auditado, "
                              "ou a conta não está liberada no portal TikTok (adicione-a em "
                              "Target Users e garanta o escopo video.publish). "
                              f"Detalhe: {code or detail}")}
        return {"ok": False, "platform": "tiktok", "status": "error",
                "error": f"TikTok HTTP {sc}: {detail}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "platform": "tiktok", "status": "error", "error": str(exc)}
