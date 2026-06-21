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
