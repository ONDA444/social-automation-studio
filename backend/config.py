"""
Configuration via Pydantic Settings.

Loads from environment / .env. Detects Railway production and normalises the
PostgreSQL URL. Everything has a safe local default so the app boots even with
an empty .env (publishing features simply stay disabled until keys are added).
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = parent of the backend/ package.
ROOT_DIR = Path(__file__).resolve().parent.parent

IS_PRODUCTION = os.getenv("RAILWAY_ENVIRONMENT") == "production"

logger = logging.getLogger(__name__)


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
    # OpenRouter aggregates DOZENS of free models on a quota INDEPENDENT of Groq's
    # and Gemini's. It's the safety net: when both of those daily free tiers are
    # exhausted (the recurring "LLM indisponível" failure), OpenRouter keeps scripts
    # flowing so jobs don't die. Free key (email signup, no phone): openrouter.ai/keys.
    openrouter_api_key: str = ""
    ollama_host: str = "http://localhost:11434"
    # Free-tier pacing — minimum requests/min spacing so a video's burst of ~8
    # LLM calls never trips the per-minute rate limit (429). Tune down if you hit
    # limits, up if your tier is higher. Groq free ~30 RPM, Gemini free ~10-15 RPM.
    groq_rpm: int = 25
    gemini_rpm: int = 10
    # Resilience: when ALL free LLM providers are momentarily exhausted (429 /
    # daily quota), a scheduled video dies at the scriptwriter. Instead of losing
    # it, _job_retry_errored resurrects it later (when a quota window reopens) up
    # to this many times. The scriptwriter aborts in seconds when the LLM is down
    # — BEFORE any render — so retries are cheap. Set 0 to disable resurrection.
    # 12 (with the growing back-off in _job_retry_errored) spreads the attempts
    # across ~37h so a job that errors in the evening survives past the 00:05 daily
    # quota reset and catches the fresh window instead of dying first.
    llm_retry_max: int = 12
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
    visuals_concurrency: int = 4         # max scenes fetched/generated in parallel

    # ---- Premium TTS (LMNT voice clone) ----
    # Your own/licensed LMNT voice, used through LMNT's official API. Set BOTH
    # keys to switch narration from edge-tts to LMNT; blank = keep edge-tts.
    lmnt_api_key: str = ""
    lmnt_voice: str = ""                 # LMNT voice id (Voices tab in app.lmnt.com)
    tts_provider: str = "auto"           # auto = LMNT when configured, else edge-tts
    # Default narration pace for free voices. pt-BR neural voices at +0% sound
    # slow/dragged; a slight boost reads as natural & energetic. Per-content
    # overrides live in NarratorAgent.RATE_BY_CONTENT; this is the fallback.
    tts_rate: str = "+8%"

    # ---- YouTube ----
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/youtube/callback"
    google_drive_redirect_uri: str = "http://localhost:8000/drive-library/auth/callback"
    google_drive_api_key: str = ""

    # ---- TikTok ----
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_redirect_uri: str = "http://localhost:8000/auth/tiktok/callback"
    # Until the app is APPROVED for the Content Posting API (it starts in
    # sandbox / "unaudited"), TikTok REJECTS public posts — an unaudited client
    # may only post SELF_ONLY (private, visible to the authorizing user). While
    # True, every TikTok post is forced to SELF_ONLY regardless of the job's
    # privacy, so the upload actually succeeds. Set TIKTOK_SANDBOX=0 after the
    # production app is approved to allow real public posting.
    tiktok_sandbox: bool = True

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
    # --- "Momento em alta" (trending) safety knobs ---
    trending_enabled: bool = True          # global kill-switch for trending auto-publish
    max_trending_per_day: int = 4          # hard cap of trending videos per channel/day
    trending_freshness_ttl_h: int = 12     # a moment older than this won't auto-publish
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
    # Cap on a manually-uploaded video (Agenda "Enviar video do PC"). Streamed to
    # disk in chunks either way, but a hard cap keeps a mistaken huge upload from
    # filling the container's ephemeral disk.
    max_manual_upload_mb: int = 500
    # Monetization CTA injected at the TOP of every YouTube description (affiliate /
    # digital product / newsletter links + FTC disclosure). The only revenue that does
    # NOT require the channel to be in the YPP. Empty = disabled. Set MONETIZATION_CTA
    # on Railway with the real links, e.g. "🔗 Ferramentas: https://...\n(links afiliados)".
    monetization_cta: str = ""
    # Extra languages to localize each YouTube video's title/description into (free
    # international reach). Comma-separated ISO codes, e.g. "en,es,hi". Empty = disabled.
    # Applied via videos.update read-modify-write (never wipes the base snippet).
    localize_languages: str = ""

    # CORS origins for the frontend (comma-separated in env, list in code).
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @model_validator(mode="after")
    def _derive_redirect_uris(self) -> "Settings":
        """Auto-fill localhost redirect URIs using APP_BASE_URL when set.
        Falls back to RAILWAY_PUBLIC_DOMAIN so Railway deployments need no manual config."""
        base = self.app_base_url.rstrip("/")
        if not base:
            railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
            if railway_domain:
                base = f"https://{railway_domain.rstrip('/')}"
        if not base:
            return self
        for attr, path in (
            ("google_redirect_uri", "/auth/youtube/callback"),
            ("google_drive_redirect_uri", "/drive-library/auth/callback"),
            ("tiktok_redirect_uri", "/auth/tiktok/callback"),
            ("meta_redirect_uri", "/auth/instagram/callback"),
        ):
            if getattr(self, attr).startswith("http://localhost"):
                setattr(self, attr, f"{base}{path}")
        return self

    @model_validator(mode="after")
    def _warn_production_risks(self) -> "Settings":
        """Loud warnings (never aborts) when prod boots with unsafe defaults.

        Aborting would risk taking down a healthy deploy, so these only WARN — but
        the risks are real and otherwise SILENT:
        - the default SECRET_KEY derives the Fernet key that encrypts every OAuth
          token; the default is public (anyone can decrypt) AND changing it later
          makes all stored tokens undecryptable (InvalidToken) — accounts go dark.
        - a SQLite database_url on Railway's ephemeral filesystem wipes all
          jobs/accounts/tokens on every redeploy.
        """
        if IS_PRODUCTION:
            if self.secret_key == "dev-insecure-change-me":
                logger.critical(
                    "SECRET_KEY is the INSECURE DEFAULT in production — set a strong, "
                    "STABLE SECRET_KEY on Railway (OAuth tokens are encrypted with a key "
                    "derived from it; keep it constant or all stored tokens are orphaned)."
                )
            if self.sqlalchemy_url.startswith("sqlite"):
                logger.critical(
                    "DATABASE_URL is SQLite in production — Railway's filesystem is "
                    "ephemeral, so jobs/accounts/tokens are WIPED on every redeploy. "
                    "Attach Postgres and set DATABASE_URL."
                )
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
