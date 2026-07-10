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
import json
import os

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
def build_auth_url(state: str = "", force_consent: bool = True) -> dict:
    err = _missing_libs()
    if err:
        return {"ok": False, "error": err}
    if not settings.google_client_id or not settings.google_client_secret:
        return {"ok": False, "error": "GOOGLE_CLIENT_ID/SECRET não configurados"}
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = settings.google_redirect_uri
    params = {
        "access_type": "offline",
        "include_granted_scopes": "true",
        "state": state,
    }
    if force_consent:
        params["prompt"] = "consent"
    url, _ = flow.authorization_url(**params)
    return {"ok": True, "auth_url": url}


def exchange_code(code: str) -> dict:
    err = _missing_libs()
    if err:
        return {"ok": False, "error": err}
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = settings.google_redirect_uri
    # Google may return previously granted scopes when include_granted_scopes=true
    # (for example Drive readonly from the ready-video library). Those extra scopes
    # are not a failed grant; oauthlib is strict by default and raises a Warning,
    # which became a 500 during reconnect. Relax only the token scope check.
    previous_relax_scope = os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE")
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    try:
        flow.fetch_token(code=code)
    finally:
        if previous_relax_scope is None:
            os.environ.pop("OAUTHLIB_RELAX_TOKEN_SCOPE", None)
        else:
            os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = previous_relax_scope
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
    import google_auth_httplib2
    import httplib2
    from googleapiclient.discovery import build

    # httplib2 has NO default socket timeout — a stalled TCP connect/read (dead
    # peer, blackholed route) blocks forever otherwise. This was the root cause
    # of jobs stuck in "publishing" indefinitely: the resumable-upload chunk loop
    # in upload_video() below has no bound of its own, so a hung socket here
    # propagated all the way up through asyncio.to_thread with no exception ever
    # raised. 300s is generous for a single chunk/refresh round-trip.
    http = google_auth_httplib2.AuthorizedHttp(_credentials(creds), http=httplib2.Http(timeout=300))
    return build("youtube", "v3", http=http, cache_discovery=False)


def _fetch_channel(creds: dict) -> dict:
    try:
        yt = _service(creds)
        resp = yt.channels().list(part="snippet", mine=True).execute()
        item = (resp.get("items") or [{}])[0]
        return {"channel_id": item.get("id"), "title": item.get("snippet", {}).get("title")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_channel failed: %s", exc)
        return {}


# ---------------------------------------------------------------- playlists
# Grouping uploads into a series/topic playlist is the cheapest session-time win
# (a returning binge-viewer is worth 5-10x). All best-effort: a playlist failure
# must NEVER affect the publish. Scope `youtube` already covers these (no re-consent).
def ensure_playlist(creds: dict, title: str, description: str = "") -> str | None:
    """Return the id of the channel playlist named `title`, creating it if missing.
    Looks up by title first (1 unit) so it never duplicates. None on any failure."""
    title = (title or "").strip()
    if not title:
        return None
    try:
        yt = _service(creds)
        req = yt.playlists().list(part="snippet", mine=True, maxResults=50)
        while req is not None:
            resp = req.execute()
            for it in resp.get("items", []):
                if (it.get("snippet", {}).get("title") or "").strip().lower() == title.lower():
                    return it["id"]
            req = yt.playlists().list_next(req, resp)
        created = yt.playlists().insert(
            part="snippet,status",
            body={"snippet": {"title": title[:150], "description": description[:5000]},
                  "status": {"privacyStatus": "public"}},
        ).execute()
        return created.get("id")
    except Exception as exc:  # noqa: BLE001
        logger.warning("ensure_playlist(%r) failed: %s", title, exc)
        return None


def add_to_playlist(creds: dict, playlist_id: str, video_id: str) -> bool:
    """Add a video to a playlist, idempotently (orphan-recovery can re-dispatch a
    publish, so skip if already present). Best-effort — returns False on any failure."""
    if not (playlist_id and video_id):
        return False
    try:
        yt = _service(creds)
        existing = yt.playlistItems().list(
            part="contentDetails", playlistId=playlist_id, videoId=video_id, maxResults=1,
        ).execute()
        if existing.get("items"):
            return True
        yt.playlistItems().insert(
            part="snippet",
            body={"snippet": {"playlistId": playlist_id,
                              "resourceId": {"kind": "youtube#video", "videoId": video_id}}},
        ).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("add_to_playlist(%s, %s) failed: %s", playlist_id, video_id, exc)
        return False


# ---------------------------------------------------------------- localizations
def set_localizations(creds: dict, video_id: str, default_language: str,
                      localizations: dict) -> bool:
    """Add localized title/description to a video for free international reach.

    READ-MODIFY-WRITE: reads the current snippet+localizations first and writes it
    back UNCHANGED except for the added languages, so title/description/tags/category
    are NEVER wiped (videos.update replaces what you send). Best-effort — a failure
    never affects the publish. One update covers ALL languages."""
    if not (video_id and localizations):
        return False
    try:
        yt = _service(creds)
        cur = yt.videos().list(part="snippet,localizations", id=video_id).execute()
        items = cur.get("items") or []
        if not items:
            return False
        snippet = items[0].get("snippet", {})
        if not snippet.get("title") or not snippet.get("categoryId"):
            return False  # update requires these; don't risk a malformed write
        loc = dict(items[0].get("localizations") or {})
        for lang, v in localizations.items():
            title = (v or {}).get("title", "").strip()
            if not title:
                continue
            loc[lang] = {"title": title[:100],
                         "description": (v.get("description") or "")[:5000]}
        if not loc:
            return False
        if default_language:
            snippet["defaultLanguage"] = default_language
        yt.videos().update(
            part="snippet,localizations",
            body={"id": video_id, "snippet": snippet, "localizations": loc},
        ).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_localizations(%s) failed: %s", video_id, exc)
        return False


# ---------------------------------------------------------------- channel branding (optimizer)
# The writable subset of brandingSettings.channel. We NEVER send title (read-only —
# channels.update returns channelTitleUpdateForbidden if it changes) or image fields
# (read-only output). Scope `youtube` already covers these — no re-consent.
_BRANDING_WRITABLE = ("keywords", "description", "country", "defaultLanguage",
                      "unsubscribedTrailer")


def _friendly_api_error(exc: Exception, action: str = "acessar o YouTube") -> str:
    """Translate noisy googleapiclient HttpError text into something useful."""
    raw = str(exc)
    lowered = raw.lower()
    reason = ""
    try:
        content = getattr(exc, "content", b"")
        if isinstance(content, bytes):
            content = content.decode("utf-8", "ignore")
        if content:
            payload = json.loads(content)
            errors = payload.get("error", {}).get("errors") or []
            reason = (errors[0].get("reason") or "") if errors else ""
            raw = payload.get("error", {}).get("message") or raw
            lowered = f"{reason} {raw}".lower()
    except Exception:  # noqa: BLE001
        pass
    if "quota" in lowered or "exceeded" in lowered:
        return (
            "Quota diaria da API do YouTube atingida. O Google bloqueou esta acao "
            f"ao tentar {action}. Aguarde o reset da quota ou solicite aumento de quota no Google Cloud."
        )
    if "forbidden" in lowered or "403" in lowered:
        return (
            "O YouTube recusou esta acao (403). Verifique se a conta conectada tem permissao "
            "para gerenciar este canal e tente reconectar o YouTube."
        )
    if any(k in lowered for k in ("invalid_grant", "token has been expired", "token_revoked")):
        return "Login do YouTube expirou. Reconecte este canal e tente novamente."
    return raw[:300]


def get_branding(creds: dict) -> dict:
    """Read the current channel identity. Returns {ok, channel_id, title, branding:{
    keywords, description, country, defaultLanguage, ...}}. Best-effort."""
    try:
        yt = _service(creds)
        resp = yt.channels().list(part="brandingSettings,snippet,id", mine=True).execute()
        items = resp.get("items") or []
        if not items:
            return {"ok": False, "error": "Nenhum canal nesta conta."}
        ch = items[0]
        chan = (ch.get("brandingSettings") or {}).get("channel") or {}
        snip = ch.get("snippet") or {}
        return {
            "ok": True,
            "channel_id": ch.get("id"),
            "title": snip.get("title") or chan.get("title"),
            "branding": {k: chan.get(k, "") for k in _BRANDING_WRITABLE},
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_branding failed: %s", exc)
        return {"ok": False, "error": _friendly_api_error(exc, "ler a identidade do canal")}


# Channel-resource fields that are read-only or deprecated/removed — echoing any of
# them back in a channels.update makes the whole call 400 "invalid argument".
_BRANDING_DROP = ("title", "defaultTab", "moderateComments", "showRelatedChannels",
                  "showBrowseView", "profileColor", "featuredChannelsTitle",
                  "featuredChannelsUrls", "trackingAnalyticsAccountId")


def update_branding(creds: dict, patch: dict) -> dict:
    """Apply a partial branding patch (any of _BRANDING_WRITABLE) via channels.update.

    READ-MODIFY-WRITE the FULL brandingSettings (so the image sub-object isn't lost),
    drop the read-only/deprecated channel fields (they 400 the call), overlay the patch.
    channels.update for brandingSettings is finicky: a single field can 400 the whole
    request, so on a badRequest we retry progressively dropping the prime suspects
    (defaultLanguage, then country) — the real value (keywords/description) still lands."""
    patch = {k: v for k, v in (patch or {}).items() if k in _BRANDING_WRITABLE and v is not None}
    if not patch:
        return {"ok": False, "error": "Nada para aplicar."}
    try:
        yt = _service(creds)
        resp = yt.channels().list(part="brandingSettings,id", mine=True).execute()
        items = resp.get("items") or []
        if not items:
            return {"ok": False, "error": "Nenhum canal nesta conta."}
        ch = items[0]
        channel_id = ch["id"]
        base_bs = dict(ch.get("brandingSettings") or {})
        base_chan = {k: v for k, v in (base_bs.get("channel") or {}).items()
                     if k not in _BRANDING_DROP}

        last_err = None
        for drops in ([], ["defaultLanguage"], ["defaultLanguage", "country"],
                      ["keywords"], ["keywords", "defaultLanguage", "country"]):
            chan = dict(base_chan)
            chan.update(patch)
            for d in drops:
                chan.pop(d, None)
            body_bs = dict(base_bs)
            body_bs["channel"] = chan
            try:
                yt.channels().update(
                    part="brandingSettings",
                    body={"id": channel_id, "brandingSettings": body_bs},
                ).execute()
                applied = {k: v for k, v in patch.items() if k not in drops}
                return {"ok": True, "channel_id": channel_id, "applied": applied,
                        "dropped": drops}
            except Exception as e:  # noqa: BLE001
                last_err = e
                msg = str(e).lower()
                if "badrequest" in msg or "invalid argument" in msg:
                    logger.info("update_branding 400 — retrying without %s", drops or "(full)")
                    continue
                raise
        return {"ok": False, "error": str(last_err)[:300]}
    except Exception as exc:  # noqa: BLE001
        logger.warning("update_branding failed: %s", exc)
        return {"ok": False, "error": _friendly_api_error(exc, "atualizar a identidade do canal")}


def list_channel_sections(creds: dict) -> list[dict]:
    """Current homepage sections (best-effort, [] on failure)."""
    try:
        yt = _service(creds)
        resp = yt.channelSections().list(part="snippet,contentDetails", mine=True).execute()
        return resp.get("items") or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_channel_sections failed: %s", exc)
        return []


def ensure_section(creds: dict, section_type: str, title: str = "",
                   position: int = 0, playlist_ids: list[str] | None = None) -> bool:
    """Insert a homepage section if no same-type section already exists. Best-effort.

    Auto types (popularUploads/recentUploads) take no contentDetails; playlist types
    need contentDetails.playlists[]. Never reshuffles a layout the user curated — only
    ADDS when that type is absent."""
    try:
        existing = list_channel_sections(creds)
        for it in existing:
            if (it.get("snippet") or {}).get("type") == section_type:
                return True  # already present — don't fight a curated layout
        snippet = {"type": section_type, "position": position}
        if title:
            snippet["title"] = title[:100]
        body = {"snippet": snippet}
        if playlist_ids:
            body["contentDetails"] = {"playlists": playlist_ids[:5]}
        yt = _service(creds)
        part = "snippet,contentDetails" if playlist_ids else "snippet"
        yt.channelSections().insert(part=part, body=body).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("ensure_section(%s) failed: %s", section_type, exc)
        return False


def set_watermark(creds: dict, channel_id: str, image_path: str,
                  entire_video: bool = True) -> bool:
    """Upload the channel branding watermark (subscribe-bug). Best-effort. Needs a PNG/
    JPEG. timing covering the whole video keeps the clickable bug always on screen."""
    if not (channel_id and image_path):
        return False
    try:
        from googleapiclient.http import MediaFileUpload

        yt = _service(creds)
        body = {"position": {"type": "corner", "cornerPosition": "topRight"}}
        if entire_video:
            body["timing"] = {"type": "offsetFromStart", "offsetMs": "0",
                              "durationMs": "999999999"}
        yt.watermarks().set(
            channelId=channel_id, body=body,
            media_body=MediaFileUpload(image_path),
        ).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_watermark failed: %s", exc)
        return False


# ---------------------------------------------------------------- upload
def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    credentials: dict,
    category_id: str = "22",
    privacy: str = "private",
    publish_at: str | None = None,
    thumbnail_path: str | None = None,
    default_language: str | None = None,
    default_audio_language: str | None = None,
    made_for_kids: bool = False,
    embeddable: bool = True,
    public_stats: bool = True,
) -> dict:
    err = _missing_libs()
    if err:
        return {"ok": False, "platform": "youtube", "error": err, "status": "library_missing"}
    try:
        from googleapiclient.http import MediaFileUpload

        yt = _service(credentials)
        # Safe default: private. STUDIO_TEST_MODE forces private so test runs
        # never publish a real public video by accident.
        import os

        if os.getenv("STUDIO_TEST_MODE") == "1":
            privacy = "private"
        status = {"privacyStatus": privacy}
        if publish_at:  # schedule -> must be private until publish_at
            status = {"privacyStatus": "private", "publishAt": publish_at}
        # Reach + compliance defaults stamped on EVERY upload — YouTube has no
        # channel-wide API for these, so we emulate them per insert.
        # selfDeclaredMadeForKids MUST be sent explicitly: a missing/dropped False
        # silently mis-declares COPPA and strips comments/cards/notifications.
        status["selfDeclaredMadeForKids"] = bool(made_for_kids)
        status["embeddable"] = bool(embeddable)
        status["publicStatsViewable"] = bool(public_stats)
        snippet = {"title": title[:100], "description": description,
                   "tags": tags[:30], "categoryId": category_id}
        # Declaring the metadata + spoken-audio language stops YouTube from
        # mis-detecting a synthetic (TTS) voice's language and burying the video's
        # regional reach — the single cheapest reach win for a faceless channel.
        if default_language:
            snippet["defaultLanguage"] = default_language
        if default_audio_language:
            snippet["defaultAudioLanguage"] = default_audio_language
        body = {"snippet": snippet, "status": status}
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        request = yt.videos().insert(part="snippet,status", body=body, media_body=media)

        # num_retries lets googleapiclient retry transient (5xx / connection-reset)
        # failures with exponential backoff instead of bubbling a one-off blip up as
        # a hard publish failure. NOTE (follow-up, needs review/test): a true TCP
        # stall can still hang next_chunk — set an httplib2 socket timeout on the
        # transport in _service() to bound it. Not done here to avoid aborting slow
        # large-video uploads on an unverifiable change to the publish path.
        response = None
        while response is None:
            _, response = request.next_chunk(num_retries=3)
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
        if "quota" in msg.lower():
            status = "quota_exceeded"
        elif any(k in msg.lower() for k in ("refresherror", "invalid_grant", "token has been expired", "token_revoked")):
            status = "auth_error"
        else:
            status = "error"
        return {"ok": False, "platform": "youtube", "error": msg, "status": status}


def upload_captions(credentials: dict, video_id: str, srt_path: str,
                    language: str = "pt-BR", name: str = "") -> dict:
    """Attach an SRT as a real YouTube caption track — search-indexable transcript +
    closed captions + free auto-translation (the cheapest international-reach lever for
    a faceless channel). Uses the broad 'youtube' scope already in SCOPES (no re-consent).
    Strictly best-effort: a failure here must NEVER affect the video publish."""
    err = _missing_libs()
    if err:
        return {"ok": False, "status": "library_missing", "error": err}
    import os
    if not (video_id and srt_path and os.path.exists(srt_path)):
        return {"ok": False, "status": "file_missing"}
    try:
        from googleapiclient.http import MediaFileUpload

        yt = _service(credentials)
        lang = (language or "pt-BR").split("-")[0]  # YouTube caption language = BCP-47 primary
        body = {"snippet": {"videoId": video_id, "language": lang,
                            "name": (name or "")[:150], "isDraft": False}}
        media = MediaFileUpload(srt_path, mimetype="application/octet-stream", resumable=False)
        resp = yt.captions().insert(part="snippet", body=body, media_body=media).execute()
        return {"ok": True, "caption_id": resp.get("id")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("caption upload failed (video %s): %s", video_id, exc)
        return {"ok": False, "status": "error", "error": str(exc)[:200]}
