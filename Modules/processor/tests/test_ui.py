"""Test UI routes — smoke tests для /admin/files/* и /ui/ static."""
import io
import os
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

FERNET = Fernet.generate_key().decode()


@pytest.fixture()
def client(tmp_path):
    media = tmp_path / "media"
    db = tmp_path / "db"
    (media / "downloads").mkdir(parents=True)
    (media / "frames").mkdir(parents=True)
    db.mkdir()

    os.environ["MEDIA_DIR"] = str(media)
    os.environ["DB_DIR"] = str(db)
    os.environ["PROCESSOR_TOKEN"] = "test-worker"
    os.environ["PROCESSOR_ADMIN_TOKEN"] = "test-admin"
    os.environ["PROCESSOR_KEY_ENCRYPTION_KEY"] = FERNET
    for k in [
        "BOOTSTRAP_ASSEMBLYAI_API_KEY",
        "BOOTSTRAP_DEEPGRAM_API_KEY",
        "BOOTSTRAP_OPENAI_WHISPER_API_KEY",
        "BOOTSTRAP_GROQ_API_KEY",
        "BOOTSTRAP_ANTHROPIC_API_KEY",
        "BOOTSTRAP_OPENAI_API_KEY",
        "BOOTSTRAP_GOOGLE_GEMINI_API_KEY",
    ]:
        os.environ.pop(k, None)

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "auth", "state", "jobs", "cache", "keys", "tasks", "clients", "prompts", "ui")):
            sys.modules.pop(mod, None)

    from main import app  # noqa: E402

    with TestClient(app) as c:
        yield c, media


ADMIN = {"X-Admin-Token": "test-admin"}


def test_ui_index_served(client):
    c, _ = client
    r = c.get("/ui/")
    assert r.status_code == 200
    assert "test UI" in r.text


def test_files_list_empty(client):
    c, _ = client
    r = c.get("/admin/files", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_files_upload_and_list(client):
    c, media = client
    files = {"file": ("test.mp4", io.BytesIO(b"fake mp4 bytes"), "video/mp4")}
    r = c.post("/admin/files/upload", files=files, headers=ADMIN)
    assert r.status_code == 201
    assert r.json()["name"] == "test.mp4"
    assert (media / "downloads" / "test.mp4").exists()

    r = c.get("/admin/files", headers=ADMIN)
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["name"] == "test.mp4"


def test_files_upload_rejects_bad_name(client):
    c, _ = client
    files = {"file": ("../evil.sh", io.BytesIO(b"x"), "text/plain")}
    r = c.post("/admin/files/upload", files=files, headers=ADMIN)
    assert r.status_code == 400


def test_files_delete(client):
    c, media = client
    (media / "downloads" / "hello.mp4").write_bytes(b"x")
    r = c.delete("/admin/files/hello.mp4", headers=ADMIN)
    assert r.status_code == 204
    assert not (media / "downloads" / "hello.mp4").exists()


def test_files_delete_not_found(client):
    c, _ = client
    r = c.delete("/admin/files/ghost.mp4", headers=ADMIN)
    assert r.status_code == 404


def test_files_requires_admin(client):
    c, _ = client
    r = c.get("/admin/files")
    assert r.status_code == 401


def test_frames_route_404_for_nonexistent(client):
    c, _ = client
    r = c.get("/admin/frames/abc123/frame_001.jpg", headers=ADMIN)
    assert r.status_code == 404


def test_frames_route_serves_file(client):
    c, media = client
    job_dir = media / "frames" / "abcdef"
    job_dir.mkdir(parents=True)
    (job_dir / "frame_001.jpg").write_bytes(b"\xff\xd8\xff")  # JPEG magic
    r = c.get("/admin/frames/abcdef/frame_001.jpg", headers=ADMIN)
    assert r.status_code == 200
    assert r.content[:3] == b"\xff\xd8\xff"


def test_frames_route_rejects_traversal(client):
    c, _ = client
    r = c.get("/admin/frames/abc/..%2Fevil.jpg", headers=ADMIN)
    assert r.status_code in (400, 404)
