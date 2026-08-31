"""Ingest видео: multipart → файл под MEDIA_DIR → video_path, затем publish."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HDR = {"X-Worker-Token": "test-worker-token"}


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


def test_upload_returns_path_and_writes_file(client):
    r = client.post(
        "/publisher/upload",
        headers=HDR,
        files={"file": ("clip.mp4", b"\x00\x01\x02fake-mp4", "video/mp4")},
    )
    assert r.status_code == 200
    vp = r.json()["video_path"]
    assert vp.endswith(".mp4")
    p = Path(vp)
    assert p.exists()
    assert p.read_bytes() == b"\x00\x01\x02fake-mp4"


def test_upload_rejects_bad_ext(client):
    r = client.post(
        "/publisher/upload",
        headers=HDR,
        files={"file": ("note.txt", b"nope", "text/plain")},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "unsupported_video_format"


def test_upload_requires_auth(client):
    r = client.post(
        "/publisher/upload",
        files={"file": ("clip.mp4", b"x", "video/mp4")},
    )
    assert r.status_code == 401


def test_upload_then_dry_run_publish(client):
    up = client.post(
        "/publisher/upload",
        headers=HDR,
        files={"file": ("clip.mp4", b"data", "video/mp4")},
    ).json()
    r = client.post(
        "/publisher/publish",
        headers=HDR,
        json={
            "video_path": up["video_path"],
            "title": "T",
            "platforms": ["vk_video"],
            "dry_run": True,
        },
    )
    assert r.status_code == 200
    assert r.json()["status"] == "dry_run"
