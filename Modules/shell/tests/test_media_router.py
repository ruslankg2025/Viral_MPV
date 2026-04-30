"""Тесты media-роутера — path-traversal защита, валидация имён, 404/200."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def media_app(tmp_path: Path, monkeypatch):
    """Изолированный FastAPI с media_router и tmpdir как media_dir."""
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_DIR", str(tmp_path / "db"))

    # Сбрасываем lru_cache settings
    from orchestrator.config import get_orchestrator_settings
    get_orchestrator_settings.cache_clear()

    from media.router import router as media_router

    app = FastAPI()
    app.include_router(media_router)
    return app, tmp_path


def _create_frame(media_dir: Path, job_id: str, name: str, content: bytes = b"\xff\xd8\xff\xe0jpeg") -> Path:
    d = media_dir / "frames" / job_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(content)
    return p


def _create_audio(media_dir: Path, job_id: str, content: bytes = b"\xff\xfbmp3") -> Path:
    d = media_dir / "audio"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{job_id}.mp3"
    p.write_bytes(content)
    return p


def test_get_frame_ok(media_app):
    app, media_dir = media_app
    job_id = "a" * 32
    _create_frame(media_dir, job_id, "frame_001.jpg", b"jpegdata")

    client = TestClient(app)
    r = client.get(f"/api/media/frames/{job_id}/frame_001.jpg")
    assert r.status_code == 200
    assert r.content == b"jpegdata"
    assert r.headers["content-type"] == "image/jpeg"
    assert "max-age=86400" in r.headers["cache-control"]


def test_get_frame_404_when_missing(media_app):
    app, _ = media_app
    job_id = "b" * 32
    client = TestClient(app)
    r = client.get(f"/api/media/frames/{job_id}/frame_001.jpg")
    assert r.status_code == 404
    assert r.json()["detail"] == "frame_not_found"


def test_invalid_job_id_400(media_app):
    """Не-hex или неправильная длина → 400."""
    app, _ = media_app
    client = TestClient(app)

    # uppercase hex (валидный hex но регекс требует lowercase)
    r = client.get(f"/api/media/frames/{'A'*32}/frame_001.jpg")
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_job_id"

    # короткий
    r = client.get(f"/api/media/frames/{'a'*16}/frame_001.jpg")
    assert r.status_code == 400


def test_invalid_filename_400(media_app):
    """Не-frame-имя или попытка traversal → 400."""
    app, _ = media_app
    job_id = "c" * 32
    client = TestClient(app)

    for bad_name in [
        "passwd",
        "frame.jpg",         # без цифр
        "frame_1.jpg",       # 1 цифра вместо 3
        "frame_0001.jpg",    # 4 цифры
        "frame_001.png",     # неправильное расширение
        "frame_001.jpg.bak", # extra suffix
    ]:
        r = client.get(f"/api/media/frames/{job_id}/{bad_name}")
        assert r.status_code == 400, f"expected 400 for {bad_name!r}, got {r.status_code}"


def test_path_traversal_blocked_via_url_encoding(media_app):
    """%2e%2e и слеши в имени должны не пройти валидацию."""
    app, _ = media_app
    job_id = "d" * 32
    client = TestClient(app)

    # FastAPI декодирует %2F в /, что ломает path matching → 404
    # В любом случае не должно вернуть 200 c содержимым внешнего файла
    r = client.get(f"/api/media/frames/{job_id}/..%2Fpasswd")
    assert r.status_code in (400, 404)


def test_get_audio_ok(media_app):
    app, media_dir = media_app
    job_id = "e" * 32
    _create_audio(media_dir, job_id, b"mp3bytes")

    client = TestClient(app)
    r = client.get(f"/api/media/audio/{job_id}.mp3")
    assert r.status_code == 200
    assert r.content == b"mp3bytes"
    assert r.headers["content-type"] == "audio/mpeg"


def test_get_audio_404(media_app):
    app, _ = media_app
    job_id = "f" * 32
    client = TestClient(app)
    r = client.get(f"/api/media/audio/{job_id}.mp3")
    assert r.status_code == 404
