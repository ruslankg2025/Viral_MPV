import os
import sys
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path):
    media = tmp_path / "media"
    db = tmp_path / "db"
    media.mkdir()
    (media / "downloads").mkdir()
    db.mkdir()

    os.environ["MEDIA_DIR"] = str(media)
    os.environ["DB_DIR"] = str(db)
    os.environ["PROCESSOR_TOKEN"] = "test-worker"
    os.environ["PROCESSOR_ADMIN_TOKEN"] = "test-admin"
    os.environ["MAX_CONCURRENT_TRANSCRIBE"] = "1"
    os.environ["MAX_CONCURRENT_VISION"] = "1"
    os.environ["PROCESSOR_KEY_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
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
        if mod.startswith(("main", "config", "auth", "state", "jobs", "cache", "keys", "tasks")):
            sys.modules.pop(mod, None)

    from main import app  # noqa: E402

    with TestClient(app) as c:
        yield c, media


AUTH = {"X-Worker-Token": "test-worker"}


def _wait_done(client, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/jobs/{job_id}", headers=AUTH)
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} not finished in {timeout}s")


def test_submit_and_run_transcribe_stub(client):
    c, media = client
    f = media / "downloads" / "fake.mp4"
    f.write_bytes(b"not a real video but exists")

    r = c.post(
        "/jobs/transcribe",
        json={"file_path": str(f)},
        headers=AUTH,
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    done = _wait_done(c, job_id)
    assert done["status"] == "done"
    assert done["result"]["stub"] is True
    assert done["result"]["payload_echo"]["file_path"] == str(f)


def test_file_not_found(client):
    c, _ = client
    r = c.post(
        "/jobs/extract-frames",
        json={"file_path": "/media/downloads/does_not_exist.mp4"},
        headers=AUTH,
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "file_not_found"


def test_file_outside_media_dir(client):
    c, _ = client
    r = c.post(
        "/jobs/extract-frames",
        json={"file_path": "C:/Windows/System32/cmd.exe"},
        headers=AUTH,
    )
    assert r.status_code == 400


def test_job_not_found(client):
    c, _ = client
    r = c.get("/jobs/nonexistent_id", headers=AUTH)
    assert r.status_code == 404


def test_healthz_reports_queue(client):
    c, _ = client
    r = c.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert "queue_depth" in body
    assert "jobs_by_status" in body
