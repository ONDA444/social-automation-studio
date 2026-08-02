"""Regression tests for the ready-video editorial curation layer.

Drive-sourced publishes used to ship the source clip byte-for-byte, only
wrapped with generated title/description/thumbnail metadata -- exactly the
"conteudo reutilizado" pattern YouTube's monetization review rejected on
ONDA444 ("a contestacao nao demonstrou uma edicao que agregasse valor").
ready_video_curation.py adds an original spoken take (long) or on-screen
commentary line (short) before publish. These tests cover: the best-effort
fallback contract (curation NEVER blocks a publish), and -- where ffmpeg is
available -- that the rendered output is actually a valid, longer (long-form)
or same-length-with-overlay (short) video.
"""
from __future__ import annotations

import asyncio
import subprocess
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend.agents import ready_video_curation as curation
from backend.config import settings


def _ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        subprocess.run(["ffprobe", "-version"], capture_output=True, timeout=10)
        return True
    except Exception:  # noqa: BLE001
        return False


def _probe(path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1", str(path)],
        capture_output=True, text=True, timeout=15,
    )
    duration = 0.0
    for line in out.stdout.splitlines():
        if line.startswith("duration="):
            try:
                duration = float(line.split("=", 1)[1])
            except ValueError:
                pass
    return {
        "has_video": "codec_type=video" in out.stdout,
        "has_audio": "codec_type=audio" in out.stdout,
        "duration": duration,
    }


class BestEffortFallbackTests(unittest.TestCase):
    """Curation must NEVER block a publish -- any failure anywhere in the
    pipeline must fall back to the untouched original path."""

    def test_disabled_flag_skips_curation_and_never_calls_the_llm(self) -> None:
        with patch.object(settings, "ready_video_curation_enabled", False), \
             patch.object(curation.llm, "complete_json", AsyncMock()) as fake_llm:
            out = curation.apply_curation_layer(
                job_id=1, local_path="/does/not/exist.mp4",
                analysis={}, context={}, video_format="long",
            )
        self.assertEqual(out, "/does/not/exist.mp4")
        fake_llm.assert_not_called()

    def test_llm_unavailable_falls_back_to_original(self) -> None:
        with patch.object(curation.llm, "available", return_value=False):
            out = curation.apply_curation_layer(
                job_id=2, local_path="/does/not/exist.mp4",
                analysis={}, context={}, video_format="long",
            )
        self.assertEqual(out, "/does/not/exist.mp4")

    def test_llm_raising_falls_back_to_original(self) -> None:
        with patch.object(curation.llm, "available", return_value=True), \
             patch.object(curation.llm, "complete_json", AsyncMock(side_effect=RuntimeError("429"))):
            out = curation.apply_curation_layer(
                job_id=3, local_path="/does/not/exist.mp4",
                analysis={}, context={}, video_format="long",
            )
        self.assertEqual(out, "/does/not/exist.mp4")

    def test_render_failure_falls_back_to_original(self) -> None:
        """The LLM take succeeds but rendering blows up (e.g. ffmpeg missing/
        corrupt input) -- the caller must still get a usable path back."""
        with patch.object(curation.llm, "available", return_value=True), \
             patch.object(curation.llm, "complete_json",
                           AsyncMock(return_value={"line": "ISSO PROVA REFLEXO"})), \
             patch.object(curation, "_apply_short_overlay", side_effect=RuntimeError("ffmpeg boom")):
            out = curation.apply_curation_layer(
                job_id=4, local_path="/does/not/exist.mp4",
                analysis={}, context={}, video_format="short",
            )
        self.assertEqual(out, "/does/not/exist.mp4")

    def test_missing_dims_and_unprobeable_file_falls_back(self) -> None:
        with patch.object(curation.llm, "available", return_value=True), \
             patch.object(curation.llm, "complete_json",
                           AsyncMock(return_value={"line": "ISSO PROVA REFLEXO"})):
            out = curation.apply_curation_layer(
                job_id=5, local_path="/does/not/exist.mp4",
                analysis={}, context={}, video_format="short",
            )
        self.assertEqual(out, "/does/not/exist.mp4")


class GenerateTakeShapeTests(unittest.TestCase):
    """The LLM commentary call must reject thin/empty output instead of
    shipping a hollow 'take' that adds no real editorial value."""

    def test_short_format_rejects_empty_line(self) -> None:
        with patch.object(curation.llm, "available", return_value=True), \
             patch.object(curation.llm, "complete_json", AsyncMock(return_value={"line": ""})):
            take = asyncio.run(curation._generate_take({}, {}, "short"))
        self.assertIsNone(take)

    def test_long_format_rejects_too_short_spoken_text(self) -> None:
        with patch.object(curation.llm, "available", return_value=True), \
             patch.object(curation.llm, "complete_json",
                           AsyncMock(return_value={"line": "ok", "spoken": "muito curto"})):
            take = asyncio.run(curation._generate_take({}, {}, "long"))
        self.assertIsNone(take)

    def test_long_format_accepts_a_real_take(self) -> None:
        spoken = " ".join(["palavra"] * 12)
        with patch.object(curation.llm, "available", return_value=True), \
             patch.object(curation.llm, "complete_json",
                           AsyncMock(return_value={"line": "a leitura curta", "spoken": spoken})):
            take = asyncio.run(curation._generate_take({}, {}, "long"))
        self.assertIsNotNone(take)
        self.assertEqual(take["spoken"], spoken)


@unittest.skipUnless(_ffmpeg_available(), "ffmpeg/ffprobe not installed")
class RealFfmpegCurationTests(unittest.TestCase):
    """End-to-end against a real (synthetic) clip -- no network calls: the LLM
    take and the TTS synth are both faked so this stays fast and hermetic."""

    def setUp(self) -> None:
        self.work = Path(settings.abs_path(settings.temp_dir)) / "test_ready_video_curation"
        self.work.mkdir(parents=True, exist_ok=True)
        self.src = self.work / "source_clip.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=4",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
             str(self.src)],
            capture_output=True, timeout=60,
        )
        self.orig = _probe(self.src)
        self.assertTrue(self.orig["has_video"] and self.orig["has_audio"])
        self.analysis = {
            "width": 640, "height": 360, "duration": self.orig["duration"],
            "has_audio": True, "summary": "clipe de teste com movimento",
            "topics": ["teste"], "entities": [], "hook": "um clipe qualquer",
        }

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.work, ignore_errors=True)
        for job_id in (101, 102, 103):
            shutil.rmtree(
                Path(settings.abs_path(settings.temp_dir)) / "ready_video_curation" / f"job_{job_id}",
                ignore_errors=True,
            )

    @staticmethod
    async def _fake_synthesize(text: str, voice: str, dst: Path) -> None:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=3", str(dst)],
            capture_output=True, timeout=30,
        )

    def test_long_format_prepends_a_narrated_intro_and_keeps_audio(self) -> None:
        spoken = "esse momento mostra um contraste que poucos videos do genero conseguem capturar de verdade"
        with patch.object(curation.llm, "available", return_value=True), \
             patch.object(curation.llm, "complete_json",
                           AsyncMock(return_value={"line": "um contraste raro", "spoken": spoken})), \
             patch.object(curation, "_synthesize", self._fake_synthesize):
            out = curation.apply_curation_layer(
                job_id=101, local_path=str(self.src), analysis=self.analysis,
                context={"niche": "teste"}, video_format="long",
            )

        self.assertNotEqual(out, str(self.src))
        self.assertTrue(Path(out).exists())
        result = _probe(out)
        self.assertTrue(result["has_video"])
        self.assertTrue(result["has_audio"])
        # Intro (~3s narration) prepended -- output must be meaningfully longer
        # than the original 4s clip, not just a copy.
        self.assertGreater(result["duration"], self.orig["duration"] + 1.5)

    def test_short_format_adds_overlay_without_changing_duration(self) -> None:
        with patch.object(curation.llm, "available", return_value=True), \
             patch.object(curation.llm, "complete_json",
                           AsyncMock(return_value={"line": "isso prova reflexo, nao sorte"})):
            out = curation.apply_curation_layer(
                job_id=102, local_path=str(self.src), analysis=self.analysis,
                context={"niche": "teste"}, video_format="short",
            )

        self.assertNotEqual(out, str(self.src))
        self.assertTrue(Path(out).exists())
        result = _probe(out)
        self.assertTrue(result["has_video"])
        self.assertTrue(result["has_audio"])
        # Overlay is burned into the existing timeline -- duration must stay
        # essentially the same (unlike the long-form intro, which extends it).
        self.assertAlmostEqual(result["duration"], self.orig["duration"], delta=0.5)

    def test_short_format_works_on_a_silent_source_clip(self) -> None:
        silent = self.work / "silent_clip.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=3",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(silent)],
            capture_output=True, timeout=60,
        )
        analysis = {**self.analysis, "has_audio": False, "duration": 3.0}
        with patch.object(curation.llm, "available", return_value=True), \
             patch.object(curation.llm, "complete_json",
                           AsyncMock(return_value={"line": "sem audio, ainda vale a leitura"})):
            out = curation.apply_curation_layer(
                job_id=103, local_path=str(silent), analysis=analysis,
                context={"niche": "teste"}, video_format="short",
            )
        self.assertTrue(Path(out).exists())
        result = _probe(out)
        self.assertTrue(result["has_video"])


if __name__ == "__main__":
    unittest.main()
