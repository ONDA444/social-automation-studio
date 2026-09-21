"""Chaves de API auto-serviço — status sem expor valores, save com override
imediato no singleton, clear volta ao .env, allowlist rejeita o resto."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from backend import runtime_settings
from backend.config import settings


class ApiKeySettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_cache = (runtime_settings._CACHE, runtime_settings._CACHE_AT,
                             runtime_settings._CACHE_LOADED)
        runtime_settings._CACHE = {}
        runtime_settings._CACHE_LOADED = True
        runtime_settings._CACHE_AT = float("inf")
        self._saved_attrs = {a: getattr(settings, a) for _, a, _ in runtime_settings._API_KEYS}

    def tearDown(self) -> None:
        (runtime_settings._CACHE, runtime_settings._CACHE_AT,
         runtime_settings._CACHE_LOADED) = self._saved_cache
        for attr, value in self._saved_attrs.items():
            setattr(settings, attr, value)

    def test_status_env_baseline_marks_configured(self) -> None:
        st = runtime_settings.get_api_key_status()
        self.assertIn("GROQ_API_KEY", st)
        # .env local tem GROQ_API_KEY preenchida -> configurada via servidor
        self.assertTrue(st["GROQ_API_KEY"]["configured"])
        self.assertEqual(st["GROQ_API_KEY"]["source"], "servidor")
        # nenhuma chave vaza valor no status
        for entry in st.values():
            self.assertNotIn("value", entry)

    def test_status_override_wins_over_env(self) -> None:
        runtime_settings._CACHE = {"apikey_GROQ_API_KEY": "gsk_painel"}
        st = runtime_settings.get_api_key_status()
        self.assertTrue(st["GROQ_API_KEY"]["configured"])
        self.assertEqual(st["GROQ_API_KEY"]["source"], "painel")
        self.assertEqual(runtime_settings.effective_api_key("GROQ_API_KEY"), "gsk_painel")

    def test_effective_falls_back_to_baseline(self) -> None:
        runtime_settings._CACHE = {}
        self.assertEqual(
            runtime_settings.effective_api_key("GROQ_API_KEY"),
            runtime_settings._ENV_BASELINE["groq_api_key"],
        )

    def test_set_rejects_unknown_key_without_touching_db(self) -> None:
        with patch.object(runtime_settings, "_write_api_rows") as w:
            with self.assertRaises(ValueError):
                runtime_settings.set_api_key("EVIL_KEY", "x")
            w.assert_not_called()

    def test_set_and_clear_orchestration(self) -> None:
        with patch.object(runtime_settings, "_write_api_rows") as w:
            with patch.object(runtime_settings, "apply_api_key_overrides") as ap:
                runtime_settings.set_api_key("GROQ_API_KEY", "  gsk_nova  ")
                w.assert_called_once_with(
                    {"apikey_GROQ_API_KEY": "gsk_nova",
                     "apikey_GROQ_API_KEY_hint": "gsk_nov… (8 caracteres)"}, [])
                ap.assert_called_once_with()
            with patch.object(runtime_settings, "apply_api_key_overrides"):
                runtime_settings.set_api_key("GROQ_API_KEY", "   ")
                w.assert_called_with(
                    {}, ["apikey_GROQ_API_KEY", "apikey_GROQ_API_KEY_hint"])

    def test_apply_overrides_mutates_singleton_and_restores(self) -> None:
        runtime_settings._CACHE = {"apikey_GROQ_API_KEY": "gsk_painel"}
        runtime_settings.apply_api_key_overrides()
        self.assertEqual(settings.groq_api_key, "gsk_painel")
        runtime_settings._CACHE = {}
        runtime_settings.apply_api_key_overrides()
        self.assertEqual(settings.groq_api_key, runtime_settings._ENV_BASELINE["groq_api_key"])

    def test_check_rejects_unknown_and_untestable(self) -> None:
        with self.assertRaises(ValueError):
            runtime_settings.check_api_key("EVIL_KEY")
        res = runtime_settings.check_api_key("GOOGLE_CLIENT_ID")
        self.assertFalse(res["ok"])

    def test_check_absent_key_reports_missing(self) -> None:
        runtime_settings._CACHE = {}
        with patch.dict(runtime_settings._ENV_BASELINE, {"pixabay_api_key": ""}):
            res = runtime_settings.check_api_key("PIXABAY_API_KEY")
        self.assertFalse(res["ok"])
        self.assertIn("ausente", res["detail"])

    def test_check_maps_401_vs_403(self) -> None:
        runtime_settings._CACHE = {"apikey_GROQ_API_KEY": "gsk_x"}
        import httpx as _httpx

        for code, needle in ((401, "rejeitada"), (403, "origem"), (200, "válida")):
            resp = _httpx.Response(code, request=_httpx.Request("GET", "https://x"))
            cli = unittest.mock.MagicMock()
            cli.__enter__.return_value = cli
            cli.get.return_value = resp
            with patch.object(_httpx, "Client", return_value=cli):
                res = runtime_settings.check_api_key("GROQ_API_KEY")
            self.assertIn(needle, res["detail"])
            self.assertEqual(res["ok"], code == 200)


if __name__ == "__main__":
    unittest.main()
