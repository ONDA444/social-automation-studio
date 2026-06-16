"""
Configuration via Pydantic Settings.

Loads from environment / .env. Detects Railway production and normalises the
PostgreSQL URL. Everything has a safe local default so the app boots even with
an empty .env (publishing features simply stay disabled until keys are added).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = parent of the backend/ package.
ROOT_DIR = Path(__file__).resolve().parent.parent

IS_PRODUCTION = os.getenv("RAILWAY_ENVIRONMENT") == "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- LLMs ----
    groq_api_key: str = ""
    gemini_api_key: str = ""
    ollama_host: str = "http://localhost:11434"
    # Ground factual topics (sports/news/events) in real web sources via Gemini +
    # Google Search before scripting, so narration states TRUE facts (real score,
    # date, names) instead of hallucinating. Needs GEMINI_API_KEY.
    research_enabled: bool = True

    # ---- Visual / B-roll ----
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    huggingface_token: str = ""
    # Pollinations now gates anonymous access (x402). A free token from
    # https://enter.pollinations.ai re-enables it; blank = skip Pollinations.
    pollinations_token: str = ""
    # Prefer REAL licensed stock VIDEO (Pexels/Pixabay) over AI stills so the
    # output looks like motion footage, not "still image + narration". When no
    # clip matches a scene, the agent falls back to an AI image (Ken Burns).
    broll_enabled: bool = True
    broll_max_width: int = 1920          # cap download resolution (avoid huge 4k files)

    # ---- Premium TTS (LMNT voice clone) ----
    # Your own/licensed LMNT voice, used through LMNT's official API. Set BOTH
    # keys to switch narration from edge-tts to LMNT; blank = keep edge-tts.
    lmnt_api_key: str = ""
    lmnt_voice: str = ""                 # LMNT voice id (Voices tab in app.lmnt.com)
    tts_provider: str = "auto"           # auto = LMNT when configured, else edge-tts

    # ---- YouTube ----
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/youtube/callback"

    # ---- TikTok ----
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_redirect_uri: str = "http://localhost:8000/auth/tiktok/callback"

    # ---- Meta / Instagram ----
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_redirect_uri: str = "http://localhost:8000/auth/instagram/callback"

    # ---- App ----
    # Public base URL of this service (e.g. https://backend-production-d314e.up.railway.app).
    # When set, redirect URIs default to this host and Instagram uses the app's
    # own /files endpoint instead of transfer.sh to expose videos publicly.
    app_base_url: str = ""
    secret_key: str = "dev-insecure-change-me"
    database_url: str = "sqlite:///./studio.db"
    redis_url: str = "redis://localhost:6379"
    temp_dir: str = "./tmp"
    output_dir: str = "./output"
    cache_dir: str = "./cache"
    assets_dir: str = "./assets"
    max_queue_size: int = 500
    # Only enqueue to Celery when a real worker is deployed. Default False so a
    # single-service deploy (Railway: just uvicorn, Redis up for events) runs the
    # pipeline IN-PROCESS instead of pushing tasks to a queue nobody consumes.
    # Set USE_CELERY=1 only if you also run `celery ... worker`.
    use_celery: bool = False
    # Fully automatic mode: scheduled videos (and manual videos tied to a channel)
    # are generated at their slot time and PUBLISHED without waiting for human
    # approval. Off by default (the approval gate stays). Set AUTO_PUBLISH=1 to
    # enable hands-off posting.
    auto_publish: bool = False
    # ffmpeg/x264 thread cap. x264 auto-detects the HOST's core count (60+ on
    # Railway), spawns that many threads, and the per-thread memory overhead OOM-kills
    # the container (ffmpeg rc=-9). Cap it low to fit the container's RAM. Override
    # with FFMPEG_THREADS if you move to a bigger box.
    ffmpeg_threads: int = 2
    # Render height (landscape). 720 keeps memory well within a small container;
    # the all-clips xfade at 1080p OOM-kills it. Bump to 1080 only on a bigger box.
    video_resolution: int = 720
    # Soft xfade transitions decode every clip of a segment at once (filter_complex
    # with all inputs) — the memory spike that OOM-kills a small container. Off by
    # default: hard cuts look clean and stream one clip at a time. Enable only on a
    # box with comfortable RAM.
    video_transitions: bool = False
    # Visibility for auto-published videos when the job itself doesn't specify one.
    # "private" is the safe default; set DEFAULT_PRIVACY=public to post publicly so
    # the videos actually reach the audience. (public|unlisted|private)
    default_privacy: str = "private"
    # Publishing model:
    #   "schedule"  -> generate a bit AHEAD of the slot and upload to YouTube as
    #                  SCHEDULED (private + publishAt = slot); YouTube turns it public
    #                  at the slot time (shows as "Agendado" in Studio).
    #   "immediate" -> generate AT the slot and publish public right away.
    publish_mode: str = "schedule"
    # When the LLM is unavailable, the scriptwriter would emit a hollow generic
    # template (no real facts). False = REJECT it and retry instead of publishing
    # garbage (real content only). Set True only if you'd rather ship a placeholder.
    allow_offline_script: bool = False
    # How many minutes before the slot to start generating (must exceed render time
    # so the upload's publishAt is still in the future). Used by "schedule" mode.
    generation_lead_minutes: int = 30
    default_tts_voice: str = "pt-BR-AntonioNeural"
    default_language: str = "pt-BR"
    log_level: str = "INFO"

    # CORS origins for the frontend (comma-separated in env, list in code).
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @model_validator(mode="after")
    def _derive_redirect_uris(self) -> "Settings":
        """Auto-fill localhost redirect URIs using APP_BASE_URL when set."""
        base = self.app_base_url.rstrip("/")
        if not base:
            return self
        for attr, path in (
            ("google_redirect_uri", "/auth/youtube/callback"),
            ("tiktok_redirect_uri", "/auth/tiktok/callback"),
            ("meta_redirect_uri", "/auth/instagram/callback"),
        ):
            if getattr(self, attr).startswith("http://localhost"):
                setattr(self, attr, f"{base}{path}")
        return self

    @property
    def is_production(self) -> bool:
        return IS_PRODUCTION

    @property
    def sqlalchemy_url(self) -> str:
        """Railway hands out postgres:// URLs; SQLAlchemy needs postgresql://."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def abs_path(self, value: str) -> Path:
        """Resolve a configured (possibly relative) path against the repo root."""
        p = Path(value)
        return p if p.is_absolute() else (ROOT_DIR / p)

    def ensure_dirs(self) -> None:
        for d in (
            self.abs_path(self.temp_dir),
            self.abs_path(self.output_dir),
            self.abs_path(self.cache_dir) / "broll",
            self.abs_path(self.cache_dir) / "thumbnails",
            self.abs_path(self.cache_dir) / "remix_refs",
            self.abs_path(self.assets_dir) / "music",
            ROOT_DIR / "logs",
        ):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
