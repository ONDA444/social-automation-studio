from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agents.manual_upload import _analyze_sync
from backend.database import Base
from backend.models import JobStatus, PlatformAccount, VideoJob


def _make_session():
    """Returns (session, sessionmaker) sharing one in-memory engine, so a test
    can patch SessionLocal to the same sessionmaker _analyze_sync uses
    internally (it opens its own session by job_id, not the caller's)."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session(), Session


class ManualUploadAnalysisTests(unittest.TestCase):
    def test_valid_video_lands_in_awaiting_approval_with_real_seo(self) -> None:
        db, Session = _make_session()
        with tempfile.TemporaryDirectory() as tmp:
            local_path = Path(tmp) / "meu_video.mp4"
            local_path.write_bytes(b"not a real video, just needs to exist")

            account = PlatformAccount(platform="youtube", display_name="Canal Teste", niche="comedia")
            db.add(account)
            db.flush()

            job = VideoJob(
                title="Vídeo enviado manualmente",
                mode="from_manual_upload",
                content_type="film_recap_ai_images",
                video_format="long",
                account_id=account.id,
                status=JobStatus.PROCESSING,
                main_video_path=str(local_path),
                video_context={"source": "manual_upload", "hint": "sobre motociclista"},
            )
            db.add(job)
            db.commit()

            fake_analysis = {
                "status": "ok",
                "analysis_source": "vision_llm",
                "aspect_ratio": "9:16",
                "summary": "Um motociclista foge da policia em alta velocidade.",
                "topics": ["motociclista", "perseguicao"],
                "hook": "A perseguicao que ninguem esperava.",
            }
            fake_seo = {
                "youtube": {
                    "title": "A perseguição que ninguém esperava #Shorts",
                    "description": "Um motociclista foge da polícia em alta velocidade.\n\n#Shorts",
                    "tags": ["motociclista", "perseguicao", "shorts"],
                    "category_id": "22",
                },
            }
            job_id = job.id
            with patch("backend.agents.manual_upload.SessionLocal", Session), patch(
                "backend.agents.ready_video_seo.build_ready_video_package",
                return_value=(fake_analysis, fake_seo, None),
            ):
                _analyze_sync(job_id)
            db.refresh(job)

            self.assertEqual(job.status, JobStatus.AWAITING_APPROVAL)
            self.assertEqual(job.approval_status, "pending")
            self.assertEqual(job.video_format, "short")
            self.assertEqual(job.shorts_paths, [str(local_path)])
            self.assertIn("ninguém esperava", job.title)
            self.assertTrue(job.seo_metadata["youtube"]["description"])
            self.assertEqual(job.video_context["seo_source"], "vision_llm")

    def test_corrupt_file_lands_in_error_not_generic_fallback(self) -> None:
        db, Session = _make_session()
        with tempfile.TemporaryDirectory() as tmp:
            local_path = Path(tmp) / "corrompido.mp4"
            local_path.write_bytes(b"garbage")

            account = PlatformAccount(platform="youtube", display_name="Canal Teste")
            db.add(account)
            db.flush()

            job = VideoJob(
                title="Vídeo enviado manualmente",
                mode="from_manual_upload",
                content_type="film_recap_ai_images",
                video_format="long",
                account_id=account.id,
                status=JobStatus.PROCESSING,
                main_video_path=str(local_path),
                video_context={"source": "manual_upload"},
            )
            db.add(job)
            db.commit()

            fake_analysis = {"status": "fallback", "analysis_source": "probe_fallback", "warnings": ["ffprobe unavailable or unreadable video"]}
            job_id = job.id
            with patch("backend.agents.manual_upload.SessionLocal", Session), patch(
                "backend.agents.ready_video_seo.build_ready_video_package",
                return_value=(fake_analysis, {}, None),
            ):
                _analyze_sync(job_id)
            db.refresh(job)

            self.assertEqual(job.status, JobStatus.ERROR)
            self.assertEqual(job.error_message, "Arquivo de vídeo inválido ou corrompido.")
            self.assertIsNone(job.seo_metadata)

    def test_missing_file_on_disk_is_an_error_not_a_hang(self) -> None:
        db, Session = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal Teste")
        db.add(account)
        db.flush()

        job = VideoJob(
            title="Vídeo enviado manualmente",
            mode="from_manual_upload",
            account_id=account.id,
            status=JobStatus.PROCESSING,
            main_video_path="/tmp/does/not/exist.mp4",
            video_context={"source": "manual_upload"},
        )
        db.add(job)
        db.commit()
        job_id = job.id

        with patch("backend.agents.manual_upload.SessionLocal", Session):
            _analyze_sync(job_id)
        db.refresh(job)

        self.assertEqual(job.status, JobStatus.ERROR)
        self.assertEqual(job.error_message, "Arquivo de vídeo não encontrado após o upload.")


if __name__ == "__main__":
    unittest.main()
