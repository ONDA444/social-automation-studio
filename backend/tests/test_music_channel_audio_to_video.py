from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agents import ready_video_seo as rvs
from backend.agents.drive_library import DriveLibraryService, FOLDER_MIME
from backend.agents.visuals import VisualsAgent
from backend.database import Base
from backend.models import ReadyVideo
from backend.config import settings


def _ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        return True
    except Exception:  # noqa: BLE001
        return False


class _FakeListRequest:
    def __init__(self, files):
        self._files = files

    def execute(self):
        return {"files": self._files}


class _FakeGetRequest:
    def __init__(self, item):
        self._item = item

    def execute(self):
        return self._item or {}


class _FakeFiles:
    def __init__(self, tree):
        self._tree = tree

    def list(self, *, q, **_kwargs):
        folder_id = q.split("'")[1]
        return _FakeListRequest(self._tree.get(folder_id, []))

    def get(self, *, fileId, **_kwargs):
        return _FakeGetRequest({"id": fileId, "name": fileId, "mimeType": FOLDER_MIME})


class _FakeDrive:
    def __init__(self, tree):
        self._tree = tree

    def files(self):
        return _FakeFiles(self._tree)


class MusicOnlyFolderIsNoLongerSkippedTests(unittest.TestCase):
    """A Drive niche folder containing only audio tracks (e.g. "royalty-free
    music") used to have EVERY file ignored during sync — a channel pointed
    at one could never publish anything. Audio files must now become real
    ReadyVideo rows (scheduler.py wraps them into a video before publish)."""

    def _make_session(self):
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine, future=True)
        return Session()

    def test_audio_files_are_imported_as_ready_video_rows(self) -> None:
        svc = _FakeDrive({
            "music-folder": [
                {"id": "track-1", "name": "Musica Romantica.mp3", "mimeType": "audio/mpeg"},
                {"id": "track-2", "name": "Musica Motivacional.wav", "mimeType": "audio/wav"},
                {"id": "not-media", "name": "readme.txt", "mimeType": "text/plain"},
            ],
        })
        db = self._make_session()
        service = DriveLibraryService(db)

        result = service._index_folder_id(
            svc, "music-folder", niche="musicas sem direitos autorais",
            content_type="film_recap_ai_images", video_format=None,
            account_id=1, recursive=False,
        )

        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["ignored_non_video"], 1)
        rows = {r.drive_file_id: r for r in db.query(ReadyVideo).all()}
        self.assertIn("track-1", rows)
        self.assertIn("track-2", rows)
        self.assertNotIn("not-media", rows)
        self.assertEqual(rows["track-1"].mime_type, "audio/mpeg")
        # No "short/reels/tiktok" token in the name -> guess_format defaults long,
        # matching a background-music video, not a vertical clip.
        self.assertEqual(rows["track-1"].video_format, "long")


@unittest.skipUnless(_ffmpeg_available(), "ffmpeg not installed")
class RenderAudioTrackAsVideoTests(unittest.TestCase):
    """Core of the new feature: YouTube has no audio-only upload, so a music
    track needs a real video stream. A still cover image held for the whole
    track length is the minimum viable wrapper."""

    def setUp(self) -> None:
        self.tmp_dir = Path(settings.abs_path(settings.temp_dir))
        self.audio_path = self.tmp_dir / "test_track_input.mp3"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             str(self.audio_path)],
            capture_output=True, timeout=30,
        )
        self.job_dir = self.tmp_dir / "ready_videos" / "job_909090"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.job_dir, ignore_errors=True)
        self.audio_path.unlink(missing_ok=True)

    async def _fake_generate_image(self, prompt, dst, w, h, label=""):
        from PIL import Image
        Image.new("RGB", (w, h), (20, 20, 40)).save(dst, "JPEG")
        return "fake"

    def test_produces_a_real_video_matching_the_audio_duration(self) -> None:
        with patch.object(VisualsAgent, "_generate_image", self._fake_generate_image):
            out_path = rvs.render_audio_track_as_video(
                909090, str(self.audio_path), "Musica Instrumental Teste", "ambiente", "long",
            )

        self.assertTrue(os.path.exists(out_path))
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1", out_path],
            capture_output=True, text=True, timeout=15,
        )
        self.assertIn("codec_type=video", probe.stdout)
        self.assertIn("codec_type=audio", probe.stdout)
        self.assertIn("duration=2.0", probe.stdout)

    def test_portrait_short_format_swaps_to_a_tall_frame(self) -> None:
        with patch.object(VisualsAgent, "_generate_image", self._fake_generate_image):
            out_path = rvs.render_audio_track_as_video(
                909090, str(self.audio_path), "Musica Instrumental Teste", "ambiente", "short",
            )
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height", "-of", "csv=p=0", out_path],
            capture_output=True, text=True, timeout=15,
        )
        width, height = (int(x) for x in probe.stdout.strip().split(","))
        self.assertGreater(height, width)  # portrait


class IsAudioReadyTests(unittest.TestCase):
    def test_matches_common_audio_extensions_and_mime_types(self) -> None:
        self.assertTrue(rvs.is_audio_ready("track.mp3", None))
        self.assertTrue(rvs.is_audio_ready("track.wav", "application/octet-stream"))
        self.assertTrue(rvs.is_audio_ready("track.unknown", "audio/ogg"))
        self.assertFalse(rvs.is_audio_ready("clip.mp4", "video/mp4"))


if __name__ == "__main__":
    unittest.main()
