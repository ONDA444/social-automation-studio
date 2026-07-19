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

    def test_drive_shorts_apply_viral_feed_packaging(self) -> None:
        seo = build_drive_seo(
            context={
                "drive_name": "Monetize - Religiosos (85).mp4",
                "folder_path": "VIDEOS RELIGIOSOS / Cortes series",
                "niche": "Videos Religiosos",
                "content_type": "reaction_commentary",
                "video_format": "short",
            },
            analysis={},
        )

        yt = seo["youtube"]
        title = yt["title"]
        self.assertIn("#Shorts", title)
        self.assertIn("momento da cena", title.lower())
        self.assertNotIn("parecia", title.lower())
        self.assertNotIn("Monetize", title)
        self.assertNotIn("biblioteca", yt["description"].lower())
        self.assertNotIn("drive", yt["description"].lower())
        self.assertIn("viral_shorts_profile", seo["feed"])
        self.assertEqual(seo["feed"]["viral_shorts_profile"]["target"], "feed_dos_shorts")
        self.assertGreaterEqual(seo["seo_score"]["breakdown"]["shorts_feed_packaging"], 5)
        self.assertLessEqual(len(title), 100)

    def test_specific_analysis_title_gets_retention_cue_without_clickbait(self) -> None:
        seo = build_drive_seo(
            context={
                "drive_name": "autografo.mp4",
                "folder_path": "VIDEOS SATISFATORIOS",
                "niche": "Videos Satisfatorios",
                "content_type": "reaction_commentary",
                "video_format": "short",
            },
            analysis={
                "analysis_source": "vision_llm",
                "summary": "Uma fa tenta conseguir um autografo durante um evento publico.",
                "topics": ["autografo", "fa", "evento"],
                "entities": ["fa", "artista"],
                "title_options": ["O autografo falhou"],
                "hook": "O autografo falhou na hora mais inesperada.",
            },
        )

        yt = seo["youtube"]
        self.assertEqual(yt["title"], "O autografo falhou na hora mais inesperada #Shorts")
        self.assertNotIn("voce nao vai acreditar", yt["title"].lower())
        self.assertEqual(yt["tags"][0], "artista")
        self.assertTrue(yt["description"].splitlines()[0].startswith("O autografo falhou"))

    def test_cartoon_drive_titles_stay_natural_not_forced_viral(self) -> None:
        sonic = build_drive_seo(
            context={
                "drive_name": "sonic.mp4",
                "folder_path": "DESENHOS ANIMADOS / Sonic",
                "niche": "Desenhos Animados",
                "content_type": "reaction_commentary",
                "video_format": "short",
            },
            analysis={
                "analysis_source": "vision_llm",
                "summary": "Sonic perde seus poderes no prisma do paradoxo em Sonic Prime.",
                "topics": ["Sonic Prime", "perda do poder", "desenho animado"],
                "entities": ["Sonic"],
                "title_options": ["Sonic perde"],
                "hook": "Sonic perde o poder no prisma do paradoxo em Sonic Prime.",
            },
        )
        orion = build_drive_seo(
            context={
                "drive_name": "orion.mp4",
                "folder_path": "DESENHOS ANIMADOS / Orion",
                "niche": "Desenhos Animados",
                "content_type": "reaction_commentary",
                "video_format": "short",
            },
            analysis={
                "analysis_source": "vision_llm",
                "summary": "Orion perde seus poderes e descobre uma nova habilidade durante a cena.",
                "topics": ["Orion", "nova habilidade", "desenho animado"],
                "entities": ["Orion"],
                "title_options": ["Epic Orion perde seu poder, mas descobre uma nova habilidade"],
                "hook": "Quando um personagem perde seu poder, mas descobre uma nova habilidade.",
            },
        )

        for seo in (sonic, orion):
            title = seo["youtube"]["title"]
            self.assertIn("#Shorts", title)
            self.assertNotIn("parecia", title.lower())
            self.assertNotIn("Epic", title)
            self.assertNotIn("...", title)
            self.assertLessEqual(len(title), 100)

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


class FallbackTemplateDiversificationTests(unittest.TestCase):
    """Regression guard for the near-zero-reach investigation: when the AI
    analysis is unavailable, title/hook/summary/tags used to be the exact
    same literal string for every video of a given content_type — a pattern
    that reads as mass-produced/templated content to YouTube's distribution
    algorithm. These must now vary by topic while staying stable per video."""

    _TOPICS = [
        {"drive_name": f"corte_{i}.mp4", "folder_path": f"NICHO {i}", "niche": f"Nicho {i}"}
        for i in range(8)
    ]

    def _seo_for(self, topic_ctx: dict, content_type: str) -> dict:
        return build_drive_seo(
            context={**topic_ctx, "content_type": content_type, "video_format": "long"},
            analysis={},
        )

    def test_film_recap_titles_vary_across_topics_not_one_fixed_string(self) -> None:
        titles = {self._seo_for(ctx, "film_recap_ai_images")["youtube"]["title"] for ctx in self._TOPICS}
        # Every title still ends with the same fixed suffix would mean the
        # fallback is one literal string again — assert real variety instead.
        self.assertGreater(len(titles), 1)

    def test_same_topic_always_produces_the_same_title(self) -> None:
        ctx = self._TOPICS[0]
        first = self._seo_for(ctx, "film_recap_ai_images")["youtube"]["title"]
        second = self._seo_for(ctx, "film_recap_ai_images")["youtube"]["title"]
        self.assertEqual(first, second)

    def test_hooks_vary_across_topics_for_a_fixed_content_type(self) -> None:
        hooks = set()
        for ctx in self._TOPICS:
            seo = self._seo_for(ctx, "true_crime_mystery")
            hooks.add(seo["feed"]["viral_shorts_profile"]["first_line"])
        self.assertGreater(len(hooks), 1)

    def test_descriptions_vary_across_topics_with_niche(self) -> None:
        summaries = set()
        for ctx in self._TOPICS:
            seo = self._seo_for(ctx, "explainer_curiosity")
            summaries.add(seo["youtube"]["description"])
        self.assertGreater(len(summaries), 1)

    def test_filler_tags_are_not_identical_across_unrelated_topics(self) -> None:
        tag_sets = set()
        for ctx in self._TOPICS:
            seo = self._seo_for(ctx, "quote_viral")
            tag_sets.add(tuple(seo["youtube"]["tags"]))
        self.assertGreater(len(tag_sets), 1)


class GeminiVisionRetryTests(unittest.TestCase):
    """A 429 (rate limit) from Gemini must be retried with backoff instead of
    immediately falling back to the fixed title template — several channels
    hitting the same quota in the same scheduler tick was identified as a
    likely cause of the fallback dominating in production."""

    def _frames_and_context(self):
        return (["frame.jpg"], {"content_type": "film_recap_ai_images", "video_format": "short"}, {})

    def test_429_is_retried_and_succeeds_on_a_later_attempt(self) -> None:
        from unittest.mock import MagicMock

        from backend.agents import ready_video_seo as mod

        rate_limited = SimpleNamespace(status_code=429)
        success = SimpleNamespace(
            status_code=200,
            json=lambda: {"candidates": [{"content": {"parts": [{"text": '{"summary": "ok"}'}]}}]},
        )
        success.raise_for_status = lambda: None
        calls = {"n": 0}

        def fake_post(*args, **kwargs):
            calls["n"] += 1
            return rate_limited if calls["n"] < 2 else success

        # MagicMock (not SimpleNamespace) is required here: the `with` statement
        # looks up __enter__/__exit__ on the TYPE, not the instance, so a plain
        # object with instance-level __enter__/__exit__ attributes is silently
        # skipped by the context-manager protocol — MagicMock configures those
        # as real dunder methods.
        client_cm = MagicMock()
        client_cm.__enter__.return_value.post = fake_post
        frames, context, analysis = self._frames_and_context()
        with patch("backend.agents.ready_video_seo.settings.gemini_api_key", "fake-key"), \
             patch("backend.agents.ready_video_seo.Path.read_bytes", return_value=b"x"), \
             patch("backend.agents.ready_video_seo.httpx.Client", return_value=client_cm), \
             patch("backend.agents.ready_video_seo.time.sleep"):
            result = mod._describe_with_gemini(frames, context, analysis)

        self.assertEqual(calls["n"], 2)
        self.assertEqual(result, {"summary": "ok"})

    def test_permanent_error_status_does_not_retry(self) -> None:
        from unittest.mock import MagicMock

        from backend.agents import ready_video_seo as mod

        forbidden = SimpleNamespace(status_code=403)
        calls = {"n": 0}

        def fake_post(*args, **kwargs):
            calls["n"] += 1
            return forbidden

        client_cm = MagicMock()
        client_cm.__enter__.return_value.post = fake_post
        frames, context, analysis = self._frames_and_context()
        with patch("backend.agents.ready_video_seo.settings.gemini_api_key", "fake-key"), \
             patch("backend.agents.ready_video_seo.Path.read_bytes", return_value=b"x"), \
             patch("backend.agents.ready_video_seo.httpx.Client", return_value=client_cm), \
             patch("backend.agents.ready_video_seo.time.sleep"):
            result = mod._describe_with_gemini(frames, context, analysis)

        self.assertEqual(calls["n"], 1)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
