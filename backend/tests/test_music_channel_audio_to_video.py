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


class FfmpegResourceCapsTests(unittest.TestCase):
    """Regression test for a REAL production failure: the first version of
    render_audio_track_as_video() built its own ffmpeg command without the
    -threads cap that video_editor.py's VENC already uses. On Railway, x264
    then spawned one thread per HOST core (60+), blew the container memory
    limit, and was SIGKILLed ~7s in — every music job failed with rc=-9 and
    an empty stderr. The cap is load-bearing, not cosmetic."""

    def _captured_cmd(self, video_format="long"):
        captured = {}

        class _FakeProc:
            returncode = 0
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["timeout"] = kwargs.get("timeout")
            # Create the expected output file so the size check passes.
            Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"fake-mp4-bytes")
            return _FakeProc()

        async def fake_generate_image(self, prompt, dst, w, h, label=""):
            from PIL import Image
            Image.new("RGB", (w, h), (0, 0, 0)).save(dst, "JPEG")
            return "fake"

        with patch.object(VisualsAgent, "_generate_image", fake_generate_image), \
             patch.object(rvs.subprocess, "run", fake_run):
            rvs.render_audio_track_as_video(
                808080, "in.mp3", "Titulo", "ambiente", video_format,
            )
        import shutil
        shutil.rmtree(
            Path(settings.abs_path(settings.temp_dir)) / "ready_videos" / "job_808080",
            ignore_errors=True,
        )
        return captured

    def test_ffmpeg_command_caps_thread_count(self) -> None:
        cmd = self._captured_cmd()["cmd"]
        self.assertIn("-threads", cmd)
        threads = int(cmd[cmd.index("-threads") + 1])
        self.assertGreaterEqual(threads, 1)
        # Must be the configured cap, never left to ffmpeg's host-core default.
        self.assertEqual(threads, max(1, settings.ffmpeg_threads))

    def test_ffmpeg_command_lowers_framerate_for_a_still_image(self) -> None:
        # A still cover at 25/30fps is ~10k identical frames for a 6-min track:
        # pure wasted encode time that also risks tripping the subprocess timeout.
        cmd = self._captured_cmd()["cmd"]
        self.assertIn("-r", cmd)
        self.assertLessEqual(int(cmd[cmd.index("-r") + 1]), 15)

    def test_timeout_is_generous_enough_for_a_full_length_track(self) -> None:
        self.assertGreaterEqual(self._captured_cmd()["timeout"], 900)


class FfmpegFailureDiagnosticsTests(unittest.TestCase):
    """The original error text was `stderr[-800:]`, but the caller truncates
    the whole composed message to 500 chars — so ffmpeg's actual error (always
    the LAST line) got cut off and only useless progress spam survived into
    the DB. The returncode also has to be visible: only a negative value
    (-9 = SIGKILL) distinguishes an OOM kill from a real ffmpeg error."""

    def test_error_message_includes_returncode_and_survives_500_char_truncation(self) -> None:
        class _FakeProc:
            returncode = -9
            # Realistic shape: lots of progress spam, real error only at the end.
            stderr = ("frame=  21 fps=5.2 q=28.0 size=0KiB speed=0.19x    \n" * 40
                       + "Conversion failed! out of memory")

        def fake_run(cmd, **kwargs):
            return _FakeProc()

        async def fake_generate_image(self, prompt, dst, w, h, label=""):
            from PIL import Image
            Image.new("RGB", (w, h), (0, 0, 0)).save(dst, "JPEG")
            return "fake"

        with patch.object(VisualsAgent, "_generate_image", fake_generate_image), \
             patch.object(rvs.subprocess, "run", fake_run):
            with self.assertRaises(RuntimeError) as ctx:
                rvs.render_audio_track_as_video(
                    707070, "in.mp3", "Titulo", "ambiente", "long",
                )

        msg = str(ctx.exception)
        self.assertIn("rc=-9", msg)
        # The part that matters must survive the caller's [:500] clamp.
        self.assertIn("out of memory", msg[:500])

        import shutil
        shutil.rmtree(
            Path(settings.abs_path(settings.temp_dir)) / "ready_videos" / "job_707070",
            ignore_errors=True,
        )


class FailingStageIsNamedAccuratelyTests(unittest.TestCase):
    """An ffmpeg failure while wrapping a music track used to be recorded as
    "Falha ao baixar video do Drive" (download and render share one try block),
    and error_messages.py then told the user it was a network/permission
    problem with Drive. That mislabel sent a real debugging session chasing
    Drive permissions while the actual fault was an OOM-killed encode."""

    def _setup(self, ready_name: str, ready_mime: str):
        from backend.models import PlatformAccount, VideoJob, JobStatus

        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine, future=True)
        db = Session()
        account = PlatformAccount(platform="youtube", display_name="Canal", niche="musicas")
        db.add(account)
        db.flush()
        ready = ReadyVideo(
            drive_file_id="track-abc", name=ready_name, mime_type=ready_mime,
            content_type="film_recap_ai_images", video_format="long",
            account_id=account.id, status="reserved",
        )
        db.add(ready)
        db.flush()
        job = VideoJob(
            title="Musica", mode="from_ready_video",
            content_type="film_recap_ai_images", video_format="long",
            account_id=account.id, status=JobStatus.PROCESSING,
            video_context={"source": "drive_ready_video", "ready_video_id": ready.id},
        )
        db.add(job)
        db.flush()
        ready.reserved_job_id = job.id
        db.commit()
        return db, account, ready, job

    def test_audio_render_failure_is_not_labelled_a_drive_download_failure(self) -> None:
        from backend import scheduler
        from backend.agents.drive_library import DriveLibraryService

        db, account, ready, job = self._setup("track.mp3", "audio/mpeg")
        drive = DriveLibraryService(db)

        with patch.object(DriveLibraryService, "download_for_job", return_value="/tmp/track.mp3"), \
             patch.object(scheduler, "_READY_VIDEO_MAX_ATTEMPTS", 99), \
             patch("backend.agents.ready_video_seo.render_audio_track_as_video",
                   side_effect=RuntimeError("ffmpeg falhou (rc=-9) ...")):
            scheduler._finalize_ready_video_job(
                db, drive, job, ready, account, title_seed="Musica", video_format="long",
            )

        db.refresh(job)
        self.assertIn("gerar video a partir do audio", job.error_message)
        self.assertNotIn("baixar video do Drive", job.error_message)

    def test_real_drive_download_failure_still_says_download(self) -> None:
        from backend import scheduler
        from backend.agents.drive_library import DriveLibraryService

        db, account, ready, job = self._setup("clip.mp4", "video/mp4")
        drive = DriveLibraryService(db)

        with patch.object(DriveLibraryService, "download_for_job",
                          side_effect=RuntimeError("403 forbidden")), \
             patch.object(scheduler, "_READY_VIDEO_MAX_ATTEMPTS", 99):
            scheduler._finalize_ready_video_job(
                db, drive, job, ready, account, title_seed="Video", video_format="long",
            )

        db.refresh(job)
        # error_messages.py keys its friendly Drive text off this exact phrase.
        self.assertIn("baixar video do Drive", job.error_message)


class MusicContentTypeOverrideTests(unittest.TestCase):
    """Regression test for a production bug found by a 5-agent low-views
    investigation: a job's content_type is decided at creation time from the
    requested theme (default "film_recap_ai_images") BEFORE it's known that
    the reserved Drive file is audio-only. Left uncorrected, music tracks
    were published on YouTube category 24 "Entertainment" instead of 10
    "Music", and inherited film-recap titles/tags/hooks for a song."""

    def _setup(self, ready_name: str, ready_mime: str):
        from backend.models import PlatformAccount, VideoJob, JobStatus

        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine, future=True)
        db = Session()
        account = PlatformAccount(platform="youtube", display_name="Canal", niche="musicas")
        db.add(account)
        db.flush()
        ready = ReadyVideo(
            drive_file_id="track-abc", name=ready_name, mime_type=ready_mime,
            content_type="film_recap_ai_images", video_format="long",
            account_id=account.id, status="reserved",
        )
        db.add(ready)
        db.flush()
        job = VideoJob(
            title="Musica", mode="from_ready_video",
            content_type="film_recap_ai_images", video_format="long",
            account_id=account.id, status=JobStatus.PROCESSING,
            video_context={"source": "drive_ready_video", "ready_video_id": ready.id},
        )
        db.add(job)
        db.flush()
        ready.reserved_job_id = job.id
        db.commit()
        return db, account, ready, job

    def test_audio_job_content_type_is_overridden_to_music(self) -> None:
        from backend import scheduler
        from backend.agents.drive_library import DriveLibraryService

        db, account, ready, job = self._setup("track.mp3", "audio/mpeg")
        drive = DriveLibraryService(db)
        fake_seo = {"youtube": {"title": "Musica: ouca agora", "description": "d", "tags": [],
                                 "category_id": "10"}, "tiktok": {}, "instagram": {}}

        with patch.object(DriveLibraryService, "download_for_job", return_value="/tmp/track.mp3"), \
             patch("backend.agents.ready_video_seo.render_audio_track_as_video",
                   return_value="/tmp/track_as_video.mp4"), \
             patch("backend.agents.ready_video_seo.build_ready_video_package",
                   return_value=({}, fake_seo, None)):
            scheduler._finalize_ready_video_job(
                db, drive, job, ready, account, title_seed="Minha Musica", video_format="long",
            )

        db.refresh(job)
        self.assertEqual(job.content_type, "music")

    def test_non_audio_job_content_type_is_left_untouched(self) -> None:
        from backend import scheduler
        from backend.agents.drive_library import DriveLibraryService

        db, account, ready, job = self._setup("clip.mp4", "video/mp4")
        drive = DriveLibraryService(db)
        fake_seo = {"youtube": {"title": "Clipe", "description": "d", "tags": [],
                                 "category_id": "24"}, "tiktok": {}, "instagram": {}}

        with patch.object(DriveLibraryService, "download_for_job", return_value="/tmp/clip.mp4"), \
             patch("backend.agents.ready_video_seo.build_ready_video_package",
                   return_value=({}, fake_seo, None)):
            scheduler._finalize_ready_video_job(
                db, drive, job, ready, account, title_seed="Clipe", video_format="long",
            )

        db.refresh(job)
        self.assertEqual(job.content_type, "film_recap_ai_images")

    def test_music_content_type_maps_to_youtube_music_category(self) -> None:
        from backend.agents.seo_agent import YT_CATEGORY
        self.assertEqual(YT_CATEGORY.get("music"), "10")

    def test_build_drive_seo_uses_music_templates_not_film_recap_wording(self) -> None:
        seo = rvs.build_drive_seo(context={
            "title_seed": "Minha Musica Instrumental", "drive_name": "Minha Musica Instrumental.mp3",
            "folder_path": "", "niche": "musicas", "account_niche": "musicas", "display_name": "Canal",
            "target_audience": "", "tone": "", "language": "pt-BR",
            "content_type": "music", "video_format": "long",
        }, analysis={})
        self.assertEqual(seo["youtube"]["category_id"], "10")
        joined = " ".join(seo["youtube"]["tags"]).lower()
        self.assertNotIn("recap de filme", joined)
        self.assertNotIn("resumo do filme", joined)


class ReadyTitleCounterStrippedTests(unittest.TestCase):
    """Regression test: Drive files disambiguated with a trailing sequence
    number (e.g. "Academia (35).mp4") leaked that raw "(N)" straight into the
    public YouTube title (title_seed outranks the already-cleaned drive_name
    in _best_topic's priority order) — visibly branding every video as
    "episode N of a mass-produced series". The two worst-performing channels
    in production both used this naming pattern."""

    def test_parenthetical_sequence_number_is_stripped(self) -> None:
        from backend.scheduler import _clean_ready_title

        self.assertEqual(_clean_ready_title("Academia (35).mp4"), "Academia")
        self.assertEqual(_clean_ready_title("Mister Cuts Fut (32).mp4"), "Mister Cuts Fut")

    def test_titles_without_a_counter_are_unaffected(self) -> None:
        from backend.scheduler import _clean_ready_title

        self.assertEqual(_clean_ready_title("Pica Pau tenta um bloco.mp4"), "Pica Pau tenta um bloco")


class FriendlyErrorForAudioRenderTests(unittest.TestCase):
    def test_audio_render_failure_gets_its_own_plain_portuguese_message(self) -> None:
        from backend.error_messages import friendly_error

        msg = friendly_error("Falha ao gerar video a partir do audio: ffmpeg falhou (rc=-9)")
        self.assertIsNotNone(msg)
        self.assertIn("música", msg.lower())
        # Must NOT reuse the misleading Drive network/permission wording.
        self.assertNotIn("permissão", msg.lower())


if __name__ == "__main__":
    unittest.main()
