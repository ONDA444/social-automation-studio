"""Filtro server-side da Fila (§19): q/status chegam ao SQL, não ficam só no
navegador — assim um vídeo fora da página carregada também é encontrado."""
from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base, get_db
from backend.models import JobStatus, VideoJob
from backend.routers import jobs as jobs_router


def _client():
    # StaticPool + check_same_thread=False: o TestClient atende em outra thread
    # e, sem pool único, o sqlite :memory: perderia as tabelas entre conexões.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    db = Session()
    db.add_all([
        VideoJob(title="Gols da rodada do Brasileirao", topic="futebol",
                 content_type="sports_highlights", status=JobStatus.PUBLISHED),
        VideoJob(title="Resumo do filme X", topic="cinema",
                 content_type="film_recap_ai_images", status=JobStatus.ERROR),
        VideoJob(title="Frase motivacional", topic=None,
                 content_type="quote_viral", status=JobStatus.QUEUED),
    ])
    db.commit()

    app = FastAPI()
    app.include_router(jobs_router.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


class QueueServerSideFilterTests(unittest.TestCase):
    def test_q_matches_title(self) -> None:
        d = _client().get("/jobs?q=brasileirao").json()
        self.assertEqual(d["count"], 1)
        self.assertEqual(d["total"], 1)
        self.assertIn("Brasileirao", d["jobs"][0]["title"])

    def test_q_matches_content_type(self) -> None:
        d = _client().get("/jobs?q=quote_viral").json()
        self.assertEqual(d["count"], 1)

    def test_q_no_match_is_empty_and_honest(self) -> None:
        d = _client().get("/jobs?q=inexistente").json()
        self.assertEqual(d["count"], 0)
        self.assertEqual(d["total"], 0)

    def test_q_combines_with_status(self) -> None:
        d = _client().get("/jobs?q=resumo&status=error").json()
        self.assertEqual(d["count"], 1)
        d = _client().get("/jobs?q=resumo&status=published").json()
        self.assertEqual(d["count"], 0)

    def test_no_filters_returns_everything(self) -> None:
        d = _client().get("/jobs").json()
        self.assertEqual(d["count"], 3)
        self.assertEqual(d["total"], 3)

    def test_blank_q_is_ignored(self) -> None:
        d = _client().get("/jobs?q=%20%20").json()
        self.assertEqual(d["count"], 3)
