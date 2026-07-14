"""
Instagram Reels via the Meta Graph API (official).

Requirements: an Instagram Professional (Creator/Business) account linked to a
Facebook Page, and a long-lived Page/User token with instagram_content_publish.

Reels need a publicly reachable video URL. We upload to a temporary public host
(transfer.sh by default; override with PUBLIC_UPLOAD_URL) then:
  create media container (media_type=REELS, video_url) -> poll status -> publish.

Rate limit: ~25 posts / 24h per account (enforced by the publisher via quota).
"""
from __future__ import annotations

import logging
import time
from urllib.parse import urlencode

import httpx

from backend.config import settings

logger = logging.getLogger("studio.instagram")


def _friendly_api_error(exc: Exception, action: str = "publicar no Instagram") -> tuple[str, str]:
    """Translate a raw Graph API error into (status, message).

    status is one of the codes the publisher already treats as terminal
    ("auth_error", "quota_exceeded") or "error" for transient/unknown failures
    that are safe to retry.
    """
    code = None
    subcode = None
    error_type = ""
    message = str(exc)
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            payload = response.json()
            err = payload.get("error") or {}
            code = err.get("code")
            subcode = err.get("error_subcode")
            error_type = (err.get("type") or "").lower()
            message = err.get("message") or message
        except Exception:  # noqa: BLE001
            pass
    lowered = f"{message} {error_type}".lower()

    # Expired/invalid token (code 190) or OAuth-type errors.
    if code == 190 or "oauthexception" in error_type or "expired" in lowered or "invalid oauth" in lowered:
        return "auth_error", "Login do Instagram/Meta expirou. Reconecte esta conta e tente novamente."
    # Permission denied (missing scope, page/IG link revoked, etc).
    if code in (10, 200, 299) or "permission" in lowered or "does not have permission" in lowered:
        return "auth_error", ("O Instagram recusou esta acao por falta de permissao. Verifique se a conta "
                               "ainda esta vinculada a Pagina/IG Business e reconecte se necessario.")
    # App/user rate limiting (codes 4, 17, 32) and the 25-posts/24h content publish limit (subcode 2207042).
    if code in (4, 17, 32) or subcode == 2207042 or "rate limit" in lowered or "too many" in lowered:
        return "quota_exceeded", ("Limite de publicacoes do Instagram atingido (rate limit / 25 posts em 24h). "
                                   "Aguarde o reset da janela de 24h antes de tentar novamente.")
    return "error", f"Falha ao {action}: {message[:300]}"


GRAPH = "https://graph.facebook.com/v19.0"
OAUTH_DIALOG = "https://www.facebook.com/v19.0/dialog/oauth"
SCOPES = "instagram_basic,instagram_content_publish,pages_show_list,business_management"


def configured() -> bool:
    return bool(settings.meta_app_id and settings.meta_app_secret)


def build_auth_url(state: str = "") -> dict:
    if not configured():
        return {"ok": False, "error": "META_APP_ID/SECRET não configurados"}
    params = {
        "client_id": settings.meta_app_id,
        "redirect_uri": settings.meta_redirect_uri,
        "scope": SCOPES,
        "response_type": "code",
        "state": state,
    }
    return {"ok": True, "auth_url": f"{OAUTH_DIALOG}?{urlencode(params)}"}


def exchange_code(code: str) -> dict:
    if not configured():
        return {"ok": False, "error": "Instagram/Meta não configurado"}
    try:
        r = httpx.get(f"{GRAPH}/oauth/access_token", params={
            "client_id": settings.meta_app_id,
            "client_secret": settings.meta_app_secret,
            "redirect_uri": settings.meta_redirect_uri,
            "code": code,
        }, timeout=30)
        r.raise_for_status()
        short = r.json().get("access_token")
        # Exchange for a long-lived token.
        ll = httpx.get(f"{GRAPH}/oauth/access_token", params={
            "grant_type": "fb_exchange_token",
            "client_id": settings.meta_app_id,
            "client_secret": settings.meta_app_secret,
            "fb_exchange_token": short,
        }, timeout=30).json()
        token = ll.get("access_token", short)
        ig = _discover_ig_account(token)
        return {"ok": True, "credentials": {"access_token": token, **ig}}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _discover_ig_account(token: str) -> dict:
    """Find the IG business account id from the user's first linked Page."""
    try:
        pages = httpx.get(f"{GRAPH}/me/accounts", params={"access_token": token}, timeout=30).json()
        for page in pages.get("data", []):
            pid = page["id"]
            page_token = page.get("access_token", token)
            info = httpx.get(f"{GRAPH}/{pid}", params={
                "fields": "instagram_business_account", "access_token": page_token,
            }, timeout=30).json()
            iga = info.get("instagram_business_account")
            if iga:
                return {"ig_user_id": iga["id"], "page_id": pid, "page_token": page_token}
    except Exception as exc:  # noqa: BLE001
        logger.warning("IG discovery failed: %s", exc)
    return {}


def _to_public_url(video_path: str) -> str | None:
    """Return a publicly reachable URL for the video.

    When APP_BASE_URL is set (Railway) the app's own /files static mount is used
    — the video is already on disk there, no external upload needed.
    Falls back to transfer.sh only when no base URL is configured.
    """
    import os
    from pathlib import Path

    base_url = settings.app_base_url.rstrip("/")
    if base_url:
        output_root = settings.abs_path(settings.output_dir).resolve()
        try:
            rel = Path(video_path).resolve().relative_to(output_root)
            return f"{base_url}/files/{rel.as_posix()}"
        except ValueError:
            pass  # video not under output_dir — fall through

    host = "https://transfer.sh"
    try:
        name = os.path.basename(video_path)
        with open(video_path, "rb") as f:
            r = httpx.put(f"{host}/{name}", content=f.read(), timeout=300)
            r.raise_for_status()
            return r.text.strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("public upload failed: %s", exc)
        return None


def upload_reel(video_path: str, caption: str, credentials: dict, public_url: str | None = None) -> dict:
    if not configured():
        return {"ok": False, "platform": "instagram", "status": "not_configured",
                "error": "Meta/Instagram não configurado."}
    # Graph API calls for an IG Business account must use the Page token, not the
    # user token — the user token lacks instagram_content_publish once the app
    # switches to page-scoped permissions, so falling back to access_token here
    # would silently fail for users who never had a page token stored.
    token = credentials.get("page_token") or credentials.get("access_token")
    ig_user = credentials.get("ig_user_id")
    if not token or not ig_user:
        return {"ok": False, "platform": "instagram", "status": "auth_error",
                "error": "Conta IG profissional não conectada — reconecte."}

    video_url = public_url or _to_public_url(video_path)
    if not video_url:
        return {"ok": False, "platform": "instagram", "status": "error",
                "error": "Não foi possível expor o vídeo em URL pública (transfer.sh)."}
    try:
        # 1) create container
        c = httpx.post(f"{GRAPH}/{ig_user}/media", params={
            "media_type": "REELS", "video_url": video_url,
            "caption": caption[:2200], "access_token": token,
        }, timeout=60)
        c.raise_for_status()
        container_id = c.json()["id"]

        # 2) poll until FINISHED (Meta transcodes async)
        for _ in range(30):
            st = httpx.get(f"{GRAPH}/{container_id}", params={
                "fields": "status_code", "access_token": token,
            }, timeout=30).json()
            if st.get("status_code") == "FINISHED":
                break
            if st.get("status_code") == "ERROR":
                return {"ok": False, "platform": "instagram", "status": "error",
                        "error": "Container de mídia falhou no Instagram."}
            time.sleep(5)

        # 3) publish
        pub = httpx.post(f"{GRAPH}/{ig_user}/media_publish", params={
            "creation_id": container_id, "access_token": token,
        }, timeout=60)
        pub.raise_for_status()
        media_id = pub.json()["id"]
        return {"ok": True, "platform": "instagram", "video_id": media_id, "status": "published"}
    except Exception as exc:  # noqa: BLE001
        status, error_msg = _friendly_api_error(exc, "publicar o Reel")
        return {"ok": False, "platform": "instagram", "status": status, "error": error_msg}
