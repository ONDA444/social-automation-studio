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
        # Regression guard: the CTA used to be PREPENDED, making it the first
        # line of the description -- the only part visible in YouTube's
        # search/suggested preview -- and burying the actual hook. Confirmed
        # in production (VideoJob.seo_metadata): 65% of published
        # descriptions had a bare CTA link as their first line. It must now
        # be appended at the end instead, exactly once regardless of retries.
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

        self.assertFalse(seo["youtube"]["description"].startswith(cta))
        self.assertTrue(seo["youtube"]["description"].strip().endswith(cta))
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

    def test_pinned_comment_varies_across_topics_and_content_types(self) -> None:
        # Regression guard: _first_comment() used to return the exact same
        # literal string ("Voce percebeu esse detalhe de primeira?") for 8 of
        # 10 content_types, with zero variation by topic. Confirmed in
        # production: 240 of 240 sampled pinned_comment/comment_play values
        # across every channel/niche were identical -- reads as automation to
        # anyone who watches more than one video from the system.
        for content_type in ("film_recap_ai_images", "reaction_commentary", "reddit_story"):
            comments = {
                self._seo_for(ctx, content_type)["youtube"]["pinned_comment"]
                for ctx in self._TOPICS
            }
            self.assertGreater(len(comments), 1, content_type)


class TitleSeedEqualsNicheCollapseTests(unittest.TestCase):
    """Regression guard for a production bug on an "academia" channel: every
    Drive file there is named "Academia (N).mp4", so title_seed (after
    scheduler.py's _clean_ready_title strips the "(N)" counter) is always the
    literal string "Academia" -- identical to the channel's niche. _best_topic
    used to accept that as the video's `topic` before ever looking at the
    vision analysis's per-video hook/summary (analysis_source=vision_llm),
    which collapsed every video's `topic` onto the same value and, since the
    title-template variant is a deterministic hash of `topic`, produced the
    exact same literal Shorts title for dozens of unrelated uploads even
    though each one had a distinct Gemini vision summary/hook."""

    def _seo_for(self, n: int) -> dict:
        return build_drive_seo(
            context={
                "title_seed": "Academia",
                "drive_name": f"Academia ({n}).mp4",
                "folder_path": "ACADEMIA",
                "niche": "Academia",
                "account_niche": "Academia",
                "content_type": "film_recap_ai_images",
                "video_format": "short",
            },
            analysis={
                "analysis_source": "vision_llm",
                "summary": f"Um treino de peito com uma tecnica especifica mostrada no video {n}.",
                "topics": [f"treino {n}", "musculacao", "academia"],
                "entities": [f"exercicio {n}"],
                "title_options": [],
                "hook": f"Esse treino {n} tem um detalhe de execucao que muita gente erra.",
            },
        )

    def test_titles_no_longer_collapse_to_the_same_literal_string(self) -> None:
        titles = {self._seo_for(n)["youtube"]["title"] for n in range(1, 12)}
        # Before the fix every one of these collapsed to the exact same
        # literal title ("Academia: o resumo direto do que aconteceu
        # #Shorts") because `topic` was always "Academia". Assert real
        # per-video variety instead.
        self.assertGreater(len(titles), 1)

    def test_title_reflects_the_specific_vision_hook_not_the_bare_niche(self) -> None:
        seo = self._seo_for(7)
        title = seo["youtube"]["title"]
        self.assertIn("treino 7", title.lower())
        self.assertNotEqual(title, "Academia: o resumo direto do que aconteceu #Shorts")

    def test_title_seed_still_wins_when_it_is_not_just_the_niche(self) -> None:
        # Sanity check that the fix is scoped: a title_seed that carries real
        # information (not equal to the niche) must still outrank hook/summary,
        # preserving the existing priority order documented in _best_topic.
        seo = build_drive_seo(
            context={
                "title_seed": "Treino de peito avancado",
                "drive_name": "Academia (99).mp4",
                "folder_path": "ACADEMIA",
                "niche": "Academia",
                "account_niche": "Academia",
                "content_type": "film_recap_ai_images",
                "video_format": "short",
            },
            analysis={
                "analysis_source": "vision_llm",
                "summary": "Um resumo totalmente diferente do titulo.",
                "hook": "Um gancho totalmente diferente do titulo.",
            },
        )
        self.assertIn("treino de peito avancado", seo["youtube"]["title"].lower())


class TitleSeedOperationalPlusRealWordCollapseTests(unittest.TestCase):
    """Regression guard for a production bug on RexZone (platform_accounts
    id=2, niche=""): its two Drive folders ("Organização" and "Limpeza") are
    both filled with files named "Monetize - Orgainzação (N).mp4" (typo is
    literal, from the Drive file itself). scheduler.py's _clean_ready_title
    strips the "(N)" counter, so title_seed becomes the constant string
    "Monetize Orgainzação" for every file in the folder. _is_operational used
    to test _title_terms(text)'s output -- which already has OPERATIONAL_WORDS
    filtered out of it -- against OPERATIONAL_WORDS, a check that can
    structurally never match anything but a fully-operational/empty text. That
    let the mixed seed "Monetize Orgainzação" ("monetize" is operational,
    "orgainzação" is not) through as non-operational, and because RexZone has
    no niche configured the "just the niche" guard never fired either, so
    `topic` collapsed to that one literal string for every video in the
    folder -- confirmed in production: 21 of the last 40 uploads got the
    exact same literal title even with distinct vision analysis per video."""

    def _seo_for(self, n: int, folder: str = "Organização") -> dict:
        return build_drive_seo(
            context={
                "title_seed": "Monetize Orgainzação",
                "drive_name": f"Monetize - Orgainzação ({n}).mp4",
                "folder_path": folder,
                "niche": "",
                "account_niche": "",
                "content_type": "film_recap_ai_images",
                "video_format": "short",
            },
            analysis={
                "analysis_source": "vision_llm",
                "summary": f"Um corte especifico numero {n} sobre um caso real mostrado no video.",
                "topics": [f"caso {n}", "monetizacao", "corte viral"],
                "entities": [f"detalhe {n}"],
                "title_options": [],
                "hook": f"O detalhe {n} desse corte e o que ninguem esperava ver.",
            },
        )

    def test_titles_no_longer_collapse_to_the_same_literal_string(self) -> None:
        titles = {self._seo_for(n)["youtube"]["title"] for n in range(1, 12)}
        self.assertGreater(len(titles), 1)

    def test_title_reflects_the_specific_vision_hook_not_the_operational_seed(self) -> None:
        seo = self._seo_for(7)
        title = seo["youtube"]["title"]
        self.assertIn("detalhe 7", title.lower())
        self.assertNotIn("orgainzação", title.lower())

    def test_is_operational_flags_mixed_operational_plus_real_word_seed(self) -> None:
        from backend.agents.ready_video_seo import _is_operational

        self.assertTrue(_is_operational("Monetize Orgainzação"))

    def test_folder_name_alone_is_treated_as_non_informative_without_niche(self) -> None:
        # Even a channel with no niche configured must not accept a
        # title_seed that is just the Drive folder's own name.
        seo = build_drive_seo(
            context={
                "title_seed": "Limpeza",
                "drive_name": "Limpeza (3).mp4",
                "folder_path": "Limpeza",
                "niche": "",
                "account_niche": "",
                "content_type": "film_recap_ai_images",
                "video_format": "short",
            },
            analysis={
                "analysis_source": "vision_llm",
                "summary": "Uma limpeza profunda em um espaco completamente destruido antes.",
                "topics": ["organizacao", "antes e depois"],
                "entities": ["quarto"],
                "title_options": [],
                "hook": "Esse espaco parecia impossivel de organizar antes disso.",
            },
        )
        self.assertNotIn("Limpeza:", seo["youtube"]["title"])


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
