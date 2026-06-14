"""
YouTube Data API v3 uploader (official) — resumable upload + thumbnail.

Quota: 10,000 units/day per project; an upload costs ~1,600 units (~6/day).
We never spin up extra Google Cloud projects to dodge quota — the publisher
guards on the account's quota and alerts the user instead.

Heavy Google libs are imported lazily so the app boots without them; install
google-api-python-client + google-auth-oauthlib to enable real uploads.
"""
from __future__ import annotations

import logging

from backend.config import settings

logger = logging.getLogger("studio.youtube")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def _missing_libs() -> str | None:
    try:
        import google_auth_oauthlib  # noqa: F401
        import googleapiclient  # noqa: F401
        return None
    except Exception:
        return ("Bibliotecas do Google ausentes. Instale: "
                "google-api-python-client google-auth-oauthlib")


# ---------------------------------------------------------------- OAuth
def build_auth_url(state: str = "") -> dict:
    err = _missing_libs()
    if err:
        return {"ok": False, "error": err}
    if not settings.google_client_id or not settings.google_client_secret:
        return {"ok": False, "error": "GOOGLE_CLIENT_ID/SECRET não configurados"}
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = settings.google_redirect_uri
    url, _ = flow.authorization_url(access_type="offline", include_granted_scopes="true",
                                    prompt="consent", state=state)
    return {"ok": True, "auth_url": url}


def exchange_code(code: str) -> dict:
    err = _missing_libs()
    if err:
        return {"ok": False, "error": err}
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = settings.google_redirect_uri
    flow.fetch_token(code=code)
    c = flow.credentials
    creds = {
        "token": c.token,
        "refresh_token": c.refresh_token,
        "token_uri": c.token_uri,
        "client_id": c.client_id,
        "client_secret": c.client_secret,
        "scopes": list(c.scopes or SCOPES),
    }
    channel = _fetch_channel(creds)
    return {"ok": True, "credentials": creds, "channel": channel}


def _client_config() -> dict:
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_redirect_uri],
        }
    }


def _credentials(creds: dict):
    from google.oauth2.credentials import Credentials

    if not creds.get("refresh_token"):
        raise RuntimeError("Conta YouTube sem refresh_token — reconecte a conta")
    return Credentials(
        token=creds.get("token"),
        refresh_token=creds.get("refresh_token"),
        token_uri=creds.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=creds.get("client_id", settings.google_client_id),
        client_secret=creds.get("client_secret", settings.google_client_secret),
        scopes=creds.get("scopes", SCOPES),
    )


def _service(creds: dict):
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=_credentials(creds), cache_discovery=False)


def _fetch_channel(creds: dict) -> dict:
    try:
        yt = _service(creds)
        resp = yt.channels().list(part="snippet", mine=True).execute()
        item = (resp.get("items") or [{}])[0]
        return {"channel_id": item.get("id"), "title": item.get("snippet", {}).get("title")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_channel failed: %s", exc)
        return {}


# ---------------------------------------------------------------- upload
def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    credentials: dict,
    category_id: str = "22",
    privacy: str = "public",
    publish_at: str | None = None,
    thumbnail_path: str | None = None,
) -> dict:
    err = _missing_libs()
    if err:
        return {"ok": False, "platform": "youtube", "error": err, "status": "library_missing"}
    try:
        from googleapiclient.http import MediaFileUpload

        yt = _service(credentials)
        status = {"privacyStatus": privacy}
        if publish_at:  # schedule -> must be private until publish_at
            status = {"privacyStatus": "private", "publishAt": publish_at}
        body = {
            "snippet": {"title": title[:100], "description": description,
                        "tags": tags[:30], "categoryId": category_id},
            "status": status,
        }
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = yt.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            _, response = request.next_chunk()
        video_id = response["id"]

        if thumbnail_path:
            try:
                yt.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(thumbnail_path)).execute()
            except Exception as exc:  # noqa: BLE001
                logger.warning("thumbnail set failed: %s", exc)

        return {"ok": True, "platform": "youtube", "video_id": video_id,
                "url": f"https://youtu.be/{video_id}", "status": "published"}
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        status = "quota_exceeded" if "quota" in msg.lower() else "error"
        return {"ok": False, "platform": "youtube", "error": msg, "status": status}
