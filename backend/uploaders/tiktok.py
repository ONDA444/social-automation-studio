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


def upload_video(video_path: str, caption: str, credentials: dict, privacy: str = "PUBLIC_TO_EVERYONE") -> dict:
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
                "privacy_level": privacy,
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
            put = httpx.put(
                upload_url,
                content=f.read(),
                headers={"Content-Range": f"bytes 0-{size - 1}/{size}",
                         "Content-Type": "video/mp4"},
                timeout=300,
            )
            put.raise_for_status()

        return {"ok": True, "platform": "tiktok", "video_id": publish_id,
                "status": "published", "note": "Processamento final no app TikTok."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "platform": "tiktok", "status": "error", "error": str(exc)}
