"""Runtime-editable settings, persisted in the `app_settings` table.

The pydantic `settings` object is read-only at runtime (loaded from .env at
boot). A few knobs, though, must be editable from the dashboard WITHOUT a
redeploy — the monetization CTA (affiliate links) and the localization
languages. Those live here: stored in the DB, read with a short cache, and
falling back to the .env value when never saved.

Pipeline agents call the effective_* helpers; the /settings router reads/writes
the raw config via get_config/update_config.
"""
from __future__ import annotations

import logging
import time

from backend.config import settings
from backend.database import SessionLocal
from backend.models.app_setting import AppSetting

logger = logging.getLogger("studio.runtime_settings")

# Keys we expose. Each maps to a default pulled from .env so a fresh install
# behaves exactly like before the DB store existed.
_DEFAULTS = {
    "monetization_cta": lambda: settings.monetization_cta or "",
    "monetization_enabled": lambda: "1" if (settings.monetization_cta or "").strip() else "0",
    "localize_languages": lambda: settings.localize_languages or "",
    "localize_enabled": lambda: "1" if (settings.localize_languages or "").strip() else "0",
    # --- Produção (§31): padrões de narração/publicação editáveis em tempo real.
    # Antes viviam só no .env; agora o dashboard pode ajustar sem redeploy. O
    # pipeline lê os effective_* abaixo, que caem no valor do .env quando nada
    # foi salvo ainda — comportamento idêntico para instalações existentes.
    "default_tts_voice": lambda: settings.default_tts_voice or "pt-BR-AntonioNeural",
    "default_language": lambda: settings.default_language or "pt-BR",
    "tts_rate": lambda: settings.tts_rate or "+8%",
    "default_privacy": lambda: settings.default_privacy or "private",
    "auto_publish": lambda: "1" if settings.auto_publish else "0",
    "trending_enabled": lambda: "1" if settings.trending_enabled else "0",
    "max_trending_per_day": lambda: str(settings.max_trending_per_day or 4),
}

_CACHE: dict[str, str] = {}
_CACHE_AT: float = 0.0
_CACHE_LOADED = False  # tracks whether _CACHE holds a real (possibly empty) load
_CACHE_TTL = 15.0  # seconds — fresh enough; a video render takes minutes anyway


def _load_raw() -> dict[str, str]:
    """All stored rows as {key: value}. Cached briefly. Never raises."""
    global _CACHE, _CACHE_AT, _CACHE_LOADED
    now = time.monotonic()
    # Must not gate on `_CACHE` truthiness alone — an empty app_settings table
    # (no rows saved yet) yields {} which is falsy, so that would bypass the
    # cache and hit the DB on every single call regardless of TTL.
    if _CACHE_LOADED and (now - _CACHE_AT) < _CACHE_TTL:
        return _CACHE
    rows: dict[str, str] = {}
    try:
        db = SessionLocal()
        try:
            for row in db.query(AppSetting).all():
                rows[row.key] = row.value
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001  (table may not exist yet on first boot)
        logger.debug("runtime_settings load failed (using env defaults): %s", exc)
    _CACHE = rows
    _CACHE_AT = now
    _CACHE_LOADED = True
    return rows


def _invalidate() -> None:
    global _CACHE_AT, _CACHE_LOADED
    _CACHE_AT = 0.0
    _CACHE_LOADED = False


def get(key: str) -> str:
    """Stored value for `key`, falling back to the .env default."""
    rows = _load_raw()
    if key in rows:
        return rows[key]
    default = _DEFAULTS.get(key)
    return default() if default else ""


def get_config() -> dict:
    """Full monetization config for the dashboard (raw values + enabled flags)."""
    return {
        "monetization_cta": get("monetization_cta"),
        "monetization_enabled": get("monetization_enabled") == "1",
        "localize_languages": get("localize_languages"),
        "localize_enabled": get("localize_enabled") == "1",
    }


def update_config(payload: dict) -> dict:
    """Persist a partial config from the dashboard. Booleans are stored as 0/1."""
    to_store: dict[str, str] = {}
    if "monetization_cta" in payload:
        to_store["monetization_cta"] = str(payload["monetization_cta"] or "")
    if "monetization_enabled" in payload:
        to_store["monetization_enabled"] = "1" if payload["monetization_enabled"] else "0"
    if "localize_languages" in payload:
        # normalize "en, es ,hi" -> "en,es,hi"
        langs = [x.strip() for x in str(payload["localize_languages"] or "").replace(";", ",").split(",") if x.strip()]
        to_store["localize_languages"] = ",".join(langs)
    if "localize_enabled" in payload:
        to_store["localize_enabled"] = "1" if payload["localize_enabled"] else "0"

    db = SessionLocal()
    try:
        for key, value in to_store.items():
            row = db.get(AppSetting, key)
            if row is None:
                db.add(AppSetting(key=key, value=value))
            else:
                row.value = value
        db.commit()
    finally:
        db.close()
    _invalidate()
    return get_config()


# --- Effective values consumed by the pipeline -----------------------------

def effective_cta() -> str:
    """The CTA to inject, or "" when the toggle is off."""
    if get("monetization_enabled") != "1":
        return ""
    return (get("monetization_cta") or "").strip()


def effective_localize_langs() -> list[str]:
    """ISO codes to localize into, or [] when the toggle is off."""
    if get("localize_enabled") != "1":
        return []
    return [x.strip() for x in (get("localize_languages") or "").split(",") if x.strip()]


# --- Produção: effective values consumidos pelo pipeline (§31) --------------

def effective_voice() -> str:
    return (get("default_tts_voice") or "pt-BR-AntonioNeural").strip()


def effective_language() -> str:
    return (get("default_language") or "pt-BR").strip()


def effective_tts_rate() -> str:
    return (get("tts_rate") or "+8%").strip()


def effective_privacy() -> str:
    value = (get("default_privacy") or "private").strip().lower()
    return value if value in ("public", "unlisted", "private") else "private"


def effective_auto_publish() -> bool:
    return get("auto_publish") == "1"


def effective_trending_enabled() -> bool:
    return get("trending_enabled") == "1"


def effective_max_trending() -> int:
    try:
        return max(1, int(get("max_trending_per_day") or "4"))
    except ValueError:
        return 4


def get_production_config() -> dict:
    """Snapshot completo dos knobs de produção para o dashboard."""
    return {
        "default_tts_voice": effective_voice(),
        "default_language": effective_language(),
        "tts_rate": effective_tts_rate(),
        "default_privacy": effective_privacy(),
        "auto_publish": effective_auto_publish(),
        "trending_enabled": effective_trending_enabled(),
        "max_trending_per_day": effective_max_trending(),
    }


_PRIVACY_VALUES = {"public", "unlisted", "private"}


def update_production_config(payload: dict) -> dict:
    """Persist knobs de produção vindos do dashboard, com validação.

    Valores inválidos (privacidade fora da lista, tts_rate sem '%', limite de
    trending < 1) são rejeitados com ValueError em vez de corromper o pipeline.
    """
    to_store: dict[str, str] = {}
    if "default_tts_voice" in payload:
        to_store["default_tts_voice"] = str(payload["default_tts_voice"] or "").strip()
    if "default_language" in payload:
        lang = str(payload["default_language"] or "").strip()
        if not lang:
            raise ValueError("default_language não pode ser vazio")
        to_store["default_language"] = lang
    if "tts_rate" in payload:
        rate = str(payload["tts_rate"] or "").strip()
        if not rate.endswith("%"):
            raise ValueError("tts_rate deve terminar em '%' (ex.: +8%, -5%, +0%)")
        to_store["tts_rate"] = rate
    if "default_privacy" in payload:
        privacy = str(payload["default_privacy"] or "").strip().lower()
        if privacy not in _PRIVACY_VALUES:
            raise ValueError(f"default_privacy inválido: {privacy!r}")
        to_store["default_privacy"] = privacy
    if "auto_publish" in payload:
        to_store["auto_publish"] = "1" if payload["auto_publish"] else "0"
    if "trending_enabled" in payload:
        to_store["trending_enabled"] = "1" if payload["trending_enabled"] else "0"
    if "max_trending_per_day" in payload:
        n = int(payload["max_trending_per_day"])
        if n < 1 or n > 50:
            raise ValueError("max_trending_per_day deve ficar entre 1 e 50")
        to_store["max_trending_per_day"] = str(n)
    if to_store:
        db = SessionLocal()
        try:
            for key, value in to_store.items():
                row = db.get(AppSetting, key)
                if row is None:
                    db.add(AppSetting(key=key, value=value))
                else:
                    row.value = value
            db.commit()
        finally:
            db.close()
        _invalidate()
    return get_production_config()
