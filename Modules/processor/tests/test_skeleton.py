import os
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("processor")
    (tmp / "media").mkdir()
    (tmp / "media" / "downloads").mkdir()
    (tmp / "db").mkdir()
    os.environ["MEDIA_DIR"] = str(tmp / "media")
    os.environ["DB_DIR"] = str(tmp / "db")
    os.environ["PROCESSOR_TOKEN"] = "test-worker"
    os.environ["PROCESSOR_ADMIN_TOKEN"] = "test-admin"
    os.environ["PROCESSOR_KEY_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "auth", "state", "jobs", "cache", "keys", "tasks")):
            sys.modules.pop(mod, None)

    from main import app  # noqa: E402

    with TestClient(app) as c:
        yield c


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "ffmpeg_available" in body
    assert "active_keys" in body


def test_jobs_require_worker_token(client):
    r = client.post("/jobs/transcribe", json={"file_path": "x"})
    assert r.status_code == 401
    r = client.post(
        "/jobs/transcribe",
        json={"file_path": "x"},
        headers={"X-Worker-Token": "wrong"},
    )
    assert r.status_code == 401
    # с правильным токеном и несуществующим файлом — 400 (прошли auth, упали на валидации)
    r = client.post(
        "/jobs/transcribe",
        json={"file_path": "does_not_exist"},
        headers={"X-Worker-Token": "test-worker"},
    )
    assert r.status_code == 400


def test_admin_requires_admin_token(client):
    r = client.get("/admin/api-keys")
    assert r.status_code == 401
    r = client.get("/admin/api-keys", headers={"X-Admin-Token": "test-admin"})
    assert r.status_code == 200
