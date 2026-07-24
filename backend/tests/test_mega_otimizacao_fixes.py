from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.agents.channel_optimizer import _checklist
from backend.agents.content_calendar import ContentCalendarAgent
from backend.agents.music_curator import MusicCuratorAgent, _TARGET_DBFS
from backend.agents.publisher import _pick_short
from backend.agents.ready_video_seo import _tags
from backend.agents.research import ResearchAgent
from backend.agents.shorts_factory import ShortsFactoryAgent
from backend.uploaders.youtube import cap_tags_to_budget


class TagCharBudgetTests(unittest.TestCase):
    """seo_agent.py/youtube.py used to cap tags by COUNT only ([:30]) — a few
    long tags (a full title, long-tail trend phrases) could push the total
    serialized length past YouTube's ~500-char limit, which makes the API
    reject/drop the whole tags field (video ships with ZERO tags)."""

    def test_short_tag_list_is_unchanged(self) -> None:
        tags = ["gol", "futebol", "melhores momentos"]
        self.assertEqual(cap_tags_to_budget(tags), tags)

    def test_long_tags_are_dropped_before_exceeding_budget(self) -> None:
        # 20 tags of 30 chars each = 600+ chars serialized, well past budget.
        tags = [f"tag numero {i:02d} bem comprida" for i in range(20)]
        out = cap_tags_to_budget(tags)
        serialized_len = len(",".join(out))
        self.assertLessEqual(serialized_len, 460)
        # Priority order preserved: the kept tags are a PREFIX of the input.
        self.assertEqual(out, tags[: len(out)])
        self.assertLess(len(out), len(tags))

    def test_count_cap_of_30_still_applies_first(self) -> None:
        tags = [f"t{i}" for i in range(50)]
        out = cap_tags_to_budget(tags, max_chars=10_000)
        self.assertEqual(len(out), 30)


class ContentCalendarPriorityTruncationTests(unittest.TestCase):
    """videos_per_day used to truncate AFTER sorting chronologically, which
    silently kept only the earliest-in-the-day hours and discarded configured
    peak hours (e.g. 19:00/21:00) whenever per_day < len(post_times)."""

    def test_fixed_slots_keeps_configured_priority_not_earliest_hours(self) -> None:
        agent = ContentCalendarAgent(db=None)
        # Prime-time hours listed FIRST (priority order), early hours last.
        post_times = ["19:00", "21:00", "08:00", "12:00"]
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Sao_Paulo")
        slots = agent._fixed_slots(post_times, per_day=2, count=2, tz=tz)
        hours = sorted(s.astimezone(tz).hour for s in slots)
        # Must keep 19h/21h (the first 2 configured), NOT 08h/12h.
        self.assertEqual(hours, [19, 21])

    def test_smart_mode_keeps_both_learned_peak_hours_even_when_adjacent(self) -> None:
        """Real measured data proving 20:00 AND 21:00 both work (1h apart) must
        not be collapsed by the anti-cluster gap filter — that filter should
        only ever apply to the BEST_TIMES_RANKED filler, never to hours already
        validated by real analytics."""
        agent = ContentCalendarAgent(db=None)
        with patch.object(ContentCalendarAgent, "_smart_times", return_value=["20:00", "21:00"]):
            cfg = SimpleNamespace(mode="smart", post_times=[], timezone="America/Sao_Paulo")
            result = agent.resolve_post_times(account_id=1, cfg=cfg, per_day=2, mode="smart")
        self.assertEqual(sorted(result), ["20:00", "21:00"])

    def test_smart_mode_fills_remaining_slots_from_best_times_when_undertaught(self) -> None:
        agent = ContentCalendarAgent(db=None)
        with patch.object(ContentCalendarAgent, "_smart_times", return_value=["20:00"]):
            cfg = SimpleNamespace(mode="smart", post_times=[], timezone="America/Sao_Paulo")
            result = agent.resolve_post_times(account_id=1, cfg=cfg, per_day=2, mode="smart")
        self.assertIn("20:00", result)
        self.assertEqual(len(result), 2)


class ShortsBestWindowAlignmentTests(unittest.TestCase):
    """The short's start used to be chosen from arbitrary equally-spaced steps
    that almost never lined up with a real scene/phrase boundary — cutting the
    narration off mid-word in the first (most retention-critical) seconds.
    Markers are themselves exact word-start timestamps (narrator.py), so using
    them directly as candidate starts fixes this with no new dependency."""

    def test_best_window_start_is_always_a_real_marker(self) -> None:
        markers = [0.0, 12.3, 27.8, 41.2, 58.9]
        start = ShortsFactoryAgent._best_window(markers, length=15.0, total=70.0)
        self.assertIn(start, markers)

    def test_best_window_picks_marker_covering_the_most_other_markers(self) -> None:
        # Window of 10s starting at 30.0 covers markers 30,32,34 (3) — strictly
        # more than any other candidate start, so the choice is unambiguous.
        markers = [0.0, 5.0, 30.0, 32.0, 34.0]
        start = ShortsFactoryAgent._best_window(markers, length=10.0, total=50.0)
        self.assertEqual(start, 30.0)

    def test_no_markers_falls_back_to_zero(self) -> None:
        self.assertEqual(ShortsFactoryAgent._best_window([], length=15.0, total=60.0), 0.0)

    def test_window_longer_than_total_returns_zero(self) -> None:
        self.assertEqual(ShortsFactoryAgent._best_window([5.0], length=90.0, total=60.0), 0.0)


class PickShortNativeMirrorFallbackTests(unittest.TestCase):
    """A job whose SOURCE was already a native Short stores its one render as
    video_<id>_main.mp4 (no _short_<n>. suffix) — self_publish_tiktok/instagram
    always returned no_short for these even though a correct vertical file
    existed, because _pick_short only matched by suffix."""

    def test_single_native_short_is_used_as_fallback(self) -> None:
        shorts = ["/out/video_42_main.mp4"]
        self.assertEqual(_pick_short(shorts, prefer=4), shorts[0])

    def test_suffix_match_still_takes_priority_when_present(self) -> None:
        shorts = ["/out/video_42_short_1.mp4", "/out/video_42_short_4.mp4"]
        self.assertEqual(_pick_short(shorts, prefer=4), shorts[1])

    def test_ambiguous_multi_file_list_without_suffix_match_returns_none(self) -> None:
        shorts = ["/out/a.mp4", "/out/b.mp4"]
        self.assertIsNone(_pick_short(shorts, prefer=4))

    def test_empty_list_returns_none(self) -> None:
        self.assertIsNone(_pick_short([], prefer=4))


class ReadyVideoGenericTagsTests(unittest.TestCase):
    """film_recap_ai_images (the system default / biggest single volume) and
    quote_viral / top_list_ranking used to fall through to a generic
    default_variants pool even with a specific title/hook already available."""

    def test_film_recap_ai_images_gets_dedicated_tags_not_generic_fallback(self) -> None:
        tags = _tags("recap", "filme de acao", [], [], "cinema",
                      "film_recap_ai_images", "short")
        generic_fallback = {"cortes", "humor", "entretenimento", "destaque", "video viral", "cena marcante"}
        self.assertTrue(set(tags) - generic_fallback,
                         "expected at least one non-generic-default tag")

    def test_quote_viral_and_top_list_ranking_have_dedicated_entries(self) -> None:
        for ct in ("quote_viral", "top_list_ranking"):
            tags = _tags("primary", "topic", [], [], "niche", ct, "short")
            self.assertTrue(len(tags) >= 8)


class ResearchRiskFlagTests(unittest.TestCase):
    """Nothing previously stopped the pipeline from generating a new video on
    the EXACT franchise/team/anime that already earned a channel a Content ID
    strike. Reuses the existing avoid_topics guardrail infra (already fed into
    channel_config by orchestrator.py) as the risk signal."""

    def _agent_with_channel_config(self, forbidden_topics):
        agent = ResearchAgent(job_id=None, context={
            "channel_config": {"guardrails": {"forbidden_topics": forbidden_topics}}
        })
        return agent

    def test_theme_matching_avoid_topics_is_flagged(self) -> None:
        agent = self._agent_with_channel_config(["Jujutsu Kaisen"])
        self.assertTrue(agent._risk_flag("Jujutsu Kaisen episodio 20 recap", "", None))

    def test_unrelated_theme_is_not_flagged(self) -> None:
        agent = self._agent_with_channel_config(["Jujutsu Kaisen"])
        self.assertFalse(agent._risk_flag("Brasil x Argentina", "", None))

    def test_no_avoid_topics_configured_never_flags(self) -> None:
        agent = self._agent_with_channel_config([])
        self.assertFalse(agent._risk_flag("qualquer tema", "", None))

    def test_match_is_case_insensitive(self) -> None:
        agent = self._agent_with_channel_config(["jujutsu kaisen"])
        self.assertTrue(agent._risk_flag("", "JUJUTSU KAISEN temporada 2", None))


class ChannelOptimizerChecklistLinkTests(unittest.TestCase):
    """Every 'Abrir no Studio' link used the literal placeholder channel id
    'UC' — 100% of clicks landed on a nonexistent/wrong channel page."""

    def test_real_channel_id_is_interpolated_into_every_link(self) -> None:
        acct = SimpleNamespace(display_name="Canal Teste", channel_id=None)
        items = _checklist(acct, channel_id="UCabc123realid")
        self.assertTrue(items, "checklist should not be empty")
        for item in items:
            self.assertIn("UCabc123realid", item["studio_url"])
            self.assertNotIn("channel/UC/", item["studio_url"])

    def test_falls_back_to_account_channel_id_when_none_passed(self) -> None:
        acct = SimpleNamespace(display_name="Canal Teste", channel_id="UCfallback")
        items = _checklist(acct, channel_id=None)
        for item in items:
            self.assertIn("UCfallback", item["studio_url"])


class MusicLoudnessNormalizationTests(unittest.TestCase):
    """Catalog tracks measured up to ~13dB apart in native loudness; editing_
    director.py then applies a FIXED volume_db offset on top of that
    un-normalised level, so the same offset produced wildly different final
    music volume depending only on which track got picked."""

    def _make_tone_file(self, tmpdir: str, gain_db: float) -> Path:
        from pydub.generators import Sine

        # Long enough that the fixed 1.5s fade-in/2.5s fade-out (applied
        # regardless of target_len) are a small fraction of the clip, so they
        # don't dominate the measured average loudness of this short test tone
        # the way they would on a near-fade-length clip.
        seg = Sine(440).to_audio_segment(duration=21000).apply_gain(gain_db)
        path = Path(tmpdir) / f"tone_{gain_db}.wav"
        seg.export(path, format="wav")
        return path

    def test_quiet_and_loud_source_tracks_converge_to_the_same_target_loudness(self) -> None:
        from pydub import AudioSegment

        agent = MusicCuratorAgent.__new__(MusicCuratorAgent)  # no ctor args needed
        with tempfile.TemporaryDirectory() as tmp:
            quiet_src = self._make_tone_file(tmp, gain_db=-35)
            loud_src = self._make_tone_file(tmp, gain_db=-6)
            quiet_dst = Path(tmp) / "quiet_out.mp3"
            loud_dst = Path(tmp) / "loud_out.mp3"

            agent._process(quiet_src, quiet_dst, target_len=20.0)
            agent._process(loud_src, loud_dst, target_len=20.0)

            quiet_out = AudioSegment.from_file(quiet_dst)
            loud_out = AudioSegment.from_file(loud_dst)
            # Both should land within a couple dB of the target and of each other —
            # before this fix they'd differ by roughly the original ~29dB gap.
            self.assertLess(abs(quiet_out.dBFS - _TARGET_DBFS), 3.0)
            self.assertLess(abs(loud_out.dBFS - _TARGET_DBFS), 3.0)
            self.assertLess(abs(quiet_out.dBFS - loud_out.dBFS), 2.0)


if __name__ == "__main__":
    unittest.main()
