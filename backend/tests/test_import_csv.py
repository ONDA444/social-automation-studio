from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import PlatformAccount, VideoJob
from backend.routers import jobs as jobs_router


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


def _csv_file(text: str) -> UploadFile:
    return UploadFile(io.BytesIO(text.encode("utf-8-sig")), filename="import.csv")


class ImportCsvTests(unittest.IsolatedAsyncioTestCase):
    async def test_row_without_title_is_skipped_silently(self) -> None:
        db = _make_session()
        csv_text = (
            "title,topic\n"
            ",linha sem titulo\n"
            "Video Valido,algum tema\n"
        )
        with patch("backend.routers.jobs.dispatch_job") as mock_dispatch:
            result = await jobs_router.import_csv(file=_csv_file(csv_text), db=db)

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["skipped"], [])
        mock_dispatch.assert_called_once()
        jobs = db.query(VideoJob).all()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "Video Valido")

    async def test_bad_account_id_is_skipped_without_aborting_the_rest(self) -> None:
        db = _make_session()
        csv_text = (
            "title,account_id\n"
            "Linha Com Conta Invalida,999999\n"
            "Linha Sem Conta,\n"
        )
        with patch("backend.routers.jobs.dispatch_job") as mock_dispatch:
            result = await jobs_router.import_csv(file=_csv_file(csv_text), db=db)

        self.assertEqual(result["count"], 1)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["row"], 1)
        self.assertIn("999999", result["skipped"][0]["reason"])
        mock_dispatch.assert_called_once()
        jobs = db.query(VideoJob).all()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "Linha Sem Conta")

    async def test_only_first_row_per_new_channel_is_gated_for_approval(self) -> None:
        db = _make_session()
        account = PlatformAccount(platform="youtube", display_name="Canal Novo", niche="geral")
        db.add(account)
        db.commit()
        csv_text = (
            "title,account_id\n"
            f"Video Debut,{account.id}\n"
            f"Video Seguinte,{account.id}\n"
        )
        with patch("backend.routers.jobs.dispatch_job") as mock_dispatch:
            result = await jobs_router.import_csv(file=_csv_file(csv_text), db=db)

        self.assertEqual(result["count"], 2)
        self.assertEqual(mock_dispatch.call_count, 2)
        jobs = db.query(VideoJob).order_by(VideoJob.id.asc()).all()
        self.assertEqual(jobs[0].title, "Video Debut")
        self.assertTrue(jobs[0].video_context.get("require_approval"))
        self.assertEqual(jobs[1].title, "Video Seguinte")
        self.assertFalse(jobs[1].video_context.get("require_approval"))

    async def test_unknown_content_type_and_mode_fall_back_to_defaults(self) -> None:
        db = _make_session()
        csv_text = (
            "title,content_type,mode\n"
            "Video Estranho,tipo_invalido,modo_invalido\n"
        )
        with patch("backend.routers.jobs.dispatch_job") as mock_dispatch:
            result = await jobs_router.import_csv(file=_csv_file(csv_text), db=db)

        self.assertEqual(result["count"], 1)
        mock_dispatch.assert_called_once()
        job = db.query(VideoJob).one()
        self.assertEqual(job.content_type, "film_recap_ai_images")
        self.assertEqual(job.mode, "from_title")

    async def test_empty_csv_is_a_clean_no_op(self) -> None:
        db = _make_session()
        with patch("backend.routers.jobs.dispatch_job") as mock_dispatch:
            result = await jobs_router.import_csv(file=_csv_file("title\n"), db=db)

        self.assertEqual(result, {"created": [], "count": 0, "skipped": []})
        mock_dispatch.assert_not_called()

    async def test_import_uses_a_single_commit_not_one_per_row(self) -> None:
        db = _make_session()
        csv_text = "title\nUm\nDois\nTres\n"
        with patch("backend.routers.jobs.dispatch_job"), \
             patch.object(db, "commit", wraps=db.commit) as mock_commit:
            await jobs_router.import_csv(file=_csv_file(csv_text), db=db)

        self.assertEqual(mock_commit.call_count, 1)


if __name__ == "__main__":
    unittest.main()
