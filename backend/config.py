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

    # ---- Visual / B-roll ----
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    huggingface_token: str = ""
    # Pollinations now gates anonymous access (x402). A free token from
    # https://enter.pollinations.ai re-enables it; blank = skip Pollinations.
    pollinations_token: str = ""

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
    secret_key: str = "dev-insecure-change-me"
    database_url: str = "sqlite:///./studio.db"
    redis_url: str = "redis://localhost:6379"
    temp_dir: str = "./tmp"
    output_dir: str = "./output"
    cache_dir: str = "./cache"
    assets_dir: str = "./assets"
    max_queue_size: int = 500
    default_tts_voice: str = "pt-BR-AntonioNeural"
    default_language: str = "pt-BR"
    log_level: str = "INFO"

    # CORS origins for the frontend (comma-separated in env, list in code).
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

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
