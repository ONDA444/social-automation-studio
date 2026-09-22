"""Drive remodel — reedita o Drive para republicação transformada, sem nunca
travar um publish. Cobertura: fallbacks (desligado, arquivo ausente, sem
ffmpeg, probe falho, render falho) + um render real num clipe minúsculo.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.agents import drive_remodel as remodel

_HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _tiny_clip(dst: Path, seconds: int = 2) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=s=320x240:d={seconds}:r=30",
         "-f", "lavfi", "-i", f"anullsrc=r=48000:d={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
         "-shortest", str(dst)],
        capture_output=True, check=True, timeout=120,
    )
    return dst


class FallbackTests(unittest.TestCase):
    def test_disabled_returns_original(self) -> None:
        self.assertEqual(
            remodel.apply_remodel(job_id=1, local_path="/x.mp4", enabled=False), "/x.mp4")

    def test_missing_file_returns_original(self) -> None:
        self.assertEqual(
            remodel.apply_remodel(job_id=1, local_path="/nao/existe.mp4"), "/nao/existe.mp4")

    def test_no_ffmpeg_returns_original(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            src = str(Path(d) / "a.mp4")
            Path(src).write_bytes(b"fake")
            with patch.object(remodel, "_ffmpeg", return_value=None):
                self.assertEqual(remodel.apply_remodel(job_id=1, local_path=src), src)

    def test_unprobeable_returns_original(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            src = str(Path(d) / "a.mp4")
            Path(src).write_bytes(b"fake")
            with patch.object(remodel, "_probe", return_value=None):
                self.assertEqual(remodel.apply_remodel(job_id=1, local_path=src), src)

    def test_render_failure_returns_original_and_cleans_up(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            src = str(Path(d) / "a.mp4")
            Path(src).write_bytes(b"fake")
            info = {"w": 320, "h": 240, "fps": 30, "duration": 2.0, "has_audio": True}
            with patch.object(remodel, "_probe", return_value=info), \
                 patch.object(remodel, "_run", return_value=False):
                self.assertEqual(remodel.apply_remodel(job_id=1, local_path=src), src)
            leftovers = [p for p in Path(d).iterdir() if p.name.startswith("remodeled_")]
            self.assertEqual(leftovers, [])


@unittest.skipUnless(_HAS_FFMPEG, "ffmpeg ausente no ambiente de teste")
class RealRenderTests(unittest.TestCase):
    def test_remodels_tiny_clip_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            src = _tiny_clip(Path(d) / "clip.mp4")
            out = remodel.apply_remodel(job_id=77, local_path=str(src), brand_text="TESTE")
            self.assertNotEqual(out, str(src))
            outp = Path(out)
            self.assertTrue(outp.name.startswith("remodeled_"))
            self.assertTrue(outp.is_file() and outp.stat().st_size > 0)
            # Bumper de 1.2s somado aos 2s originais + áudio preservado.
            info = remodel._probe(out)
            self.assertIsNotNone(info)
            self.assertTrue(info["has_audio"])
            self.assertGreater(info["duration"], 2.5)
            # Pixels realmente mudaram (não é cópia byte a byte).
            self.assertNotEqual(outp.read_bytes()[:4096], src.read_bytes()[:4096])


if __name__ == "__main__":
    unittest.main()
