"""Testes da Media Library (routers/media_library.py).

Garante que só entram na listagem arquivos que EXISTEM de verdade no disco
(nada de mídia fantasma) e que o filtro por kind funciona.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base, get_db
from backend.models import JobStatus, VideoJob
from backend.routers import media_library


def _make_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture()
def client(tmp_path):
    Session = _make_db()
    app = FastAPI()
    app.include_router(media_library.router)

    def _override():
        db = Session
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override

    # Job 1: vídeo + thumb reais no disco
    video = tmp_path / "video1.mp4"
    video.write_bytes(b"x" * 2_000_000)
    thumb = tmp_path / "thumb1.png"
    thumb.write_bytes(b"y" * 500_000)
    # Job 2: shorts com um arquivo ausente (não deve listar)
    short = tmp_path / "short1.mp4"
    short.write_bytes(b"z" * 1_000_000)

    db = Session
    db.add(VideoJob(title="Job com midia", status=JobStatus.PUBLISHED,
                    main_video_path=str(video), thumbnail_path=str(thumb)))
    db.add(VideoJob(title="Job short parcial", status=JobStatus.PUBLISHED,
                    shorts_paths=[str(short), str(tmp_path / "nao_existe.mp4")]))
    db.add(VideoJob(title="Job sem nada", status=JobStatus.AWAITING_APPROVAL))
    db.add(VideoJob(title="Job em fila", status=JobStatus.QUEUED,
                    main_video_path=str(tmp_path / "fantasma.mp4")))
    db.commit()

    yield TestClient(app), tmp_path


def test_lists_only_existing_files(client):
    c, _ = client
    d = c.get("/media/library").json()
    paths = [i["path"] for i in d["items"]]
    assert any(p.endswith("video1.mp4") for p in paths)
    assert any(p.endswith("thumb1.png") for p in paths)
    assert any(p.endswith("short1.mp4") for p in paths)
    # ausentes / fantasma não aparecem
    assert not any("nao_existe" in p for p in paths)
    assert not any("fantasma" in p for p in paths)
    assert d["count"] == 3
    assert d["total_mb"] == pytest.approx(3.5, abs=0.1)


def test_kind_filter(client):
    c, _ = client
    d = c.get("/media/library?kind=video").json()
    assert d["count"] == 1
    assert d["items"][0]["kind"] == "video"
    d = c.get("/media/library?kind=thumb").json()
    assert d["count"] == 1
    d = c.get("/media/library?kind=short").json()
    assert d["count"] == 1


def test_size_measured_from_disk(client):
    c, tmp_path = client
    d = c.get("/media/library?kind=video").json()
    item = d["items"][0]
    assert item["size_mb"] == pytest.approx(2.0, abs=0.1)
    assert Path(item["path"]).is_file()
