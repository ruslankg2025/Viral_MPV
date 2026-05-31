"""Integration: publish → запись в БД → статус через FastAPI TestClient."""
import httpx
import pytest
from fastapi.testclient import TestClient

HDR = {"X-Worker-Token": "test-worker-token"}
ADMIN_HDR = {"X-Admin-Token": "test-admin-token"}


@pytest.fixture
def client(monkeypatch):
    # Гарантия: НИКАКОГО HTTP к api.vk.com в этих тестах (всё dry-run).
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"UNEXPECTED HTTP: {request.url}")

    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        kwargs.pop("timeout", None)
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    import main

    with TestClient(main.app) as c:
        yield c


def test_healthz(client):
    r = client.get("/publisher/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_auth_required(client):
    r = client.get("/publisher/publications")
    assert r.status_code == 401


def test_publish_dry_run_writes_db(client):
    r = client.post("/publisher/publish", headers=HDR, json={
        "video_path": "/m/v.mp4", "title": "Hello", "description": "world",
        "tags": ["viral"], "platforms": ["vk_video", "vk_clips"], "dry_run": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "dry_run"
    assert len(body["publication_ids"]) == 2

    # Проверяем записи в БД.
    lst = client.get("/publisher/publications", headers=HDR).json()
    assert len(lst) == 2
    for pub in lst:
        assert pub["status"] == "dry_run"
        assert pub["external_id"].startswith("dry-run-")
        assert pub["title"] == "Hello"
        assert pub["tags"] == ["viral"]
        assert pub["published_at"] is None


def test_publish_default_dry_run_when_flag_omitted(client):
    # DEFAULT_DRY_RUN=true в conftest → даже без dry_run флага не идём в сеть.
    r = client.post("/publisher/publish", headers=HDR, json={
        "video_path": "/m/v.mp4", "title": "X", "platforms": ["vk_video"],
    })
    assert r.status_code == 200
    assert r.json()["status"] == "dry_run"


def test_filters_and_get_single(client):
    client.post("/publisher/publish", headers=HDR, json={
        "video_path": "/m/v.mp4", "title": "A", "platforms": ["vk_video"], "dry_run": True,
    })
    pid = client.get("/publisher/publications", headers=HDR).json()[0]["id"]

    single = client.get(f"/publisher/publications/{pid}", headers=HDR)
    assert single.status_code == 200
    assert single.json()["id"] == pid

    filtered = client.get("/publisher/publications?platform=vk_clips", headers=HDR).json()
    assert filtered == []


def test_schedule_creates_scheduled(client):
    r = client.post("/publisher/schedule", headers=HDR, json={
        "video_path": "/m/v.mp4", "title": "Later",
        "platforms": ["vk_video"], "scheduled_at": "2999-01-01T00:00:00+00:00",
    })
    assert r.status_code == 200
    assert r.json()["status"] == "scheduled"
    pub = client.get("/publisher/publications?status=scheduled", headers=HDR).json()
    assert len(pub) == 1
    assert pub[0]["scheduled_at"] == "2999-01-01T00:00:00+00:00"


def test_retry(client):
    client.post("/publisher/publish", headers=HDR, json={
        "video_path": "/m/v.mp4", "title": "R", "platforms": ["vk_video"], "dry_run": True,
    })
    pid = client.get("/publisher/publications", headers=HDR).json()[0]["id"]
    r = client.post(f"/publisher/retry/{pid}", headers=HDR)
    assert r.status_code == 200
    assert r.json()["status"] == "dry_run"


def test_retry_404(client):
    r = client.post("/publisher/retry/nonexistent", headers=HDR)
    assert r.status_code == 404


def test_admin_config(client):
    r = client.get("/publisher/admin/config", headers=ADMIN_HDR)
    assert r.status_code == 200
    assert r.json()["default_dry_run"] is True
    # admin требует admin-token, не worker-token
    assert client.get("/publisher/admin/config", headers=HDR).status_code == 401
