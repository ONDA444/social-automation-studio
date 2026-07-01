from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.agents.ready_video_seo import build_drive_seo
from backend.scheduler import _NO_AUTO_RETRY_MARKERS, _ready_video_seo


class ReadyVideoSeoTests(unittest.TestCase):
    def test_fallback_uses_niche_when_filename_is_operational(self) -> None:
        seo = build_drive_seo(
            context={
                "drive_name": "Legendado (1).mp4",
                "folder_path": "VIDEOS MEMES / DESENHOS ANIMADOS",
                "niche": "Desenhos Animados",
                "content_type": "reaction_commentary",
                "video_format": "short",
            },
            analysis={},
        )

        yt = seo["youtube"]
        self.assertIn("Desenhos Animados", yt["title"])
        self.assertIn("#Shorts", yt["title"])
        self.assertNotIn("Legendado", yt["title"])
        self.assertNotIn("biblioteca", yt["description"].lower())
        self.assertNotIn("drive", yt["description"].lower())
        self.assertGreaterEqual(len(yt["tags"]), 8)

    def test_analysis_metadata_drives_specific_title(self) -> None:
        seo = build_drive_seo(
            context={
                "drive_name": "Legendado (1).mp4",
                "folder_path": "VIDEOS MEMES / DESENHOS ANIMADOS",
                "niche": "Desenhos Animados",
                "content_type": "reaction_commentary",
                "video_format": "short",
            },
            analysis={
                "analysis_source": "vision_llm",
                "summary": "Cena de humor com Woody em uma situacao policial.",
                "topics": ["desenho animado", "humor", "nostalgia"],
                "entities": ["Woody"],
                "title_options": ["Essa cena do Woody nunca envelhece"],
                "hook": "Essa cena do Woody nunca envelhece.",
            },
        )

        yt = seo["youtube"]
        self.assertEqual(yt["title"], "Essa cena do Woody nunca envelhece #Shorts")
        self.assertEqual(yt["tags"][0], "woody")
        self.assertEqual(seo["seo_score"]["verdict"], "drive_video_strong")

    def test_legacy_scheduler_helper_no_longer_uses_internal_description(self) -> None:
        ready = SimpleNamespace(
            name="export_1080p_final.mp4",
            folder_path="SAUDE / PRONTO PARA POSTAR",
            niche="Saude",
        )
        account = SimpleNamespace(
            display_name="Canal Saude",
            niche="Saude",
            drive_niche="Saude",
            target_audience="adultos",
        )

        seo = _ready_video_seo("Video pronto", ready, account, "explainer_curiosity", "long")
        description = seo["youtube"]["description"].lower()
        self.assertNotIn("biblioteca", description)
        self.assertNotIn("video pronto", description)
        self.assertNotIn("drive", description)

    def test_retry_markers_keep_auth_failures_parked(self) -> None:
        markers = " ".join(_NO_AUTO_RETRY_MARKERS)
        self.assertIn("invalid_grant", markers)
        self.assertIn("expired or revoked", markers)
        self.assertIn("credenciais conectadas", markers)

    def test_legacy_scheduler_helper_applies_monetization_cta_once(self) -> None:
        ready = SimpleNamespace(name="Academia (1).mp4", folder_path="ACADEMIA", niche="Academia")
        account = SimpleNamespace(
            display_name="Canal Fitness",
            niche="Academia",
            drive_niche="Academia",
            target_audience="mulheres",
        )
        cta = "Links de afiliado: https://example.com"

        with patch("backend.runtime_settings.effective_cta", return_value=cta), \
             patch("backend.runtime_settings.effective_localize_langs", return_value=[]):
            seo = _ready_video_seo("Academia", ready, account, "motivational_speech", "short")
            seo_again = _ready_video_seo("Academia", ready, account, "motivational_speech", "short")

        self.assertTrue(seo["youtube"]["description"].startswith(cta))
        self.assertEqual(seo["youtube"]["description"].count(cta), 1)
        self.assertEqual(seo_again["youtube"]["description"].count(cta), 1)


if __name__ == "__main__":
    unittest.main()
