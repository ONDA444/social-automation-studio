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
