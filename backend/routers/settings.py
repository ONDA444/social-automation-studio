"""Runtime settings the dashboard can edit without a redeploy.

Currently the monetization config: the affiliate/product CTA injected at the top
of every YouTube description, and the languages each video is localized into.
Persisted in app_settings (see backend.runtime_settings)."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend import runtime_settings

router = APIRouter(prefix="/settings", tags=["settings"])


class MonetizationConfig(BaseModel):
    monetization_cta: str | None = None
    monetization_enabled: bool | None = None
    localize_languages: str | None = None
    localize_enabled: bool | None = None


@router.get("/monetization")
def get_monetization() -> dict:
    return runtime_settings.get_config()


@router.put("/monetization")
def put_monetization(cfg: MonetizationConfig) -> dict:
    # Only forward the fields the client actually sent (exclude_unset keeps a
    # partial update partial — toggling one switch won't wipe the CTA text).
    payload = cfg.model_dump(exclude_unset=True)
    return runtime_settings.update_config(payload)


# --- Produção (§31): padrões de narração/publicação editáveis sem redeploy ---

class ProductionConfig(BaseModel):
    default_tts_voice: str | None = None
    default_language: str | None = None
    tts_rate: str | None = None
    default_privacy: str | None = None
    auto_publish: bool | None = None
    trending_enabled: bool | None = None
    max_trending_per_day: int | None = None


@router.get("/production")
def get_production() -> dict:
    return runtime_settings.get_production_config()


@router.put("/production")
def put_production(cfg: ProductionConfig) -> dict:
    payload = cfg.model_dump(exclude_unset=True)
    try:
        return runtime_settings.update_production_config(payload)
    except ValueError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail=str(exc)) from exc
