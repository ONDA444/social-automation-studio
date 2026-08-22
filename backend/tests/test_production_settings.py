"""Settings de Produção (§31) — knobs editáveis em runtime com validação.

Garante: defaults caem no .env, updates persistem e os effective_* refletem,
valores inválidos são rejeitados (nunca corrompem o pipeline).
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from backend import runtime_settings


class ProductionSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        # Cache global: isolar cada teste dos valores reais do studio.db.
        self._saved = (runtime_settings._CACHE, runtime_settings._CACHE_AT,
                       runtime_settings._CACHE_LOADED)
        runtime_settings._CACHE = {}
        runtime_settings._CACHE_LOADED = True
        runtime_settings._CACHE_AT = float("inf")

    def tearDown(self) -> None:
        (runtime_settings._CACHE, runtime_settings._CACHE_AT,
         runtime_settings._CACHE_LOADED) = self._saved

    def test_defaults_fall_back_to_env(self) -> None:
        self.assertEqual(runtime_settings.effective_voice(), "pt-BR-AntonioNeural")
        self.assertEqual(runtime_settings.effective_language(), "pt-BR")
        self.assertEqual(runtime_settings.effective_tts_rate(), "+8%")
        self.assertEqual(runtime_settings.effective_privacy(), "private")
        self.assertFalse(runtime_settings.effective_auto_publish())
        self.assertTrue(runtime_settings.effective_trending_enabled())
        self.assertEqual(runtime_settings.effective_max_trending(), 4)

    def test_effective_values_read_stored_rows(self) -> None:
        runtime_settings._CACHE = {
            "default_tts_voice": "pt-BR-FranciscaNeural",
            "default_language": "en-US",
            "tts_rate": "+15%",
            "default_privacy": "public",
            "auto_publish": "1",
            "trending_enabled": "0",
            "max_trending_per_day": "7",
        }
        self.assertEqual(runtime_settings.effective_voice(), "pt-BR-FranciscaNeural")
        self.assertEqual(runtime_settings.effective_language(), "en-US")
        self.assertEqual(runtime_settings.effective_tts_rate(), "+15%")
        self.assertEqual(runtime_settings.effective_privacy(), "public")
        self.assertTrue(runtime_settings.effective_auto_publish())
        self.assertFalse(runtime_settings.effective_trending_enabled())
        self.assertEqual(runtime_settings.effective_max_trending(), 7)

    def test_invalid_privacy_falls_back_to_private(self) -> None:
        runtime_settings._CACHE = {"default_privacy": "secret"}
        self.assertEqual(runtime_settings.effective_privacy(), "private")

    def test_max_trending_garbage_falls_back(self) -> None:
        runtime_settings._CACHE = {"max_trending_per_day": "abc"}
        self.assertEqual(runtime_settings.effective_max_trending(), 4)

    def test_update_validates_privacy(self) -> None:
        with self.assertRaises(ValueError):
            runtime_settings.update_production_config({"default_privacy": "hidden"})

    def test_update_validates_tts_rate_suffix(self) -> None:
        with self.assertRaises(ValueError):
            runtime_settings.update_production_config({"tts_rate": "8"})

    def test_update_validates_language_not_empty(self) -> None:
        with self.assertRaises(ValueError):
            runtime_settings.update_production_config({"default_language": "  "})

    def test_update_validates_trending_bounds(self) -> None:
        with self.assertRaises(ValueError):
            runtime_settings.update_production_config({"max_trending_per_day": 0})
        with self.assertRaises(ValueError):
            runtime_settings.update_production_config({"max_trending_per_day": 999})

    def test_update_persists_and_reflects(self) -> None:
        # Persiste de verdade (sqlite em memória via patch de SessionLocal).
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from backend.database import Base
        from backend.models.app_setting import AppSetting  # noqa: F401 — registra a tabela

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)

        with patch.object(runtime_settings, "SessionLocal", Session):
            runtime_settings._invalidate()
            cfg = runtime_settings.update_production_config({
                "auto_publish": True,
                "default_privacy": "unlisted",
                "max_trending_per_day": 6,
            })
            self.assertTrue(cfg["auto_publish"])
            self.assertEqual(cfg["default_privacy"], "unlisted")
            self.assertEqual(cfg["max_trending_per_day"], 6)
            # Reflete nos effective_* dentro da mesma sessão cacheada
            runtime_settings._invalidate()
            self.assertTrue(runtime_settings.effective_auto_publish())
            self.assertEqual(runtime_settings.effective_privacy(), "unlisted")
            # Linha real no banco
            row = Session().get(AppSetting, "auto_publish")
            self.assertEqual(row.value, "1")
