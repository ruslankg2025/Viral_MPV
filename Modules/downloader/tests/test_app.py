import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    """Поднимает downloader-app с временным media_dir.

    НЕ чистит sys.modules — это ломает class identity для стратегий
    в других тестах. Вместо этого: monkeypatch env + cache_clear() для
    pydantic-settings lru_cache.
    """
    media = tmp_path / "media"
    db = tmp_path / "db"
    fixtures = tmp_path / "fixtures"
    media.mkdir()
    (media / "downloads").mkdir()
    db.mkdir()
    fixtures.mkdir()

    fixture_file = fixtures / "test_fixture.mp4"
    fixture_file.write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 2048)

    monkeypatch.setenv("DOWNLOADER_TOKEN", "test-token")
    monkeypatch.setenv("MEDIA_DIR", str(media))
    monkeypatch.setenv("DB_DIR", str(db))
    monkeypatch.setenv("FIXTURE_PATH", str(fixture_file))
    monkeypatch.setenv("STUB_MODE", "true")
    monkeypatch.setenv("MAX_CONCURRENT", "1")

    root = Path(__file__).resolve().parents[1]
    # Ставим downloader первым — защита от коллизии с shell при совместном запуске
    if str(root) in sys.path:
        sys.path.remove(str(root))
    sys.path.insert(0, str(root))

    # Сбрасываем кеш модулей этого сервиса (мог остаться от shell/tests или предыдущего теста).
    # Критично: jobs.router держит `from state import state` — если его не сбросить,
    # роутер будет класть задачи в старый (остановленный) queue.
    # НЕ чистим strategies.* — их классы используют __globals__ старого dict,
    # и patch() перестаёт работать в соседних тестах если модуль переимпортировать.
    _purge = {"main", "config", "auth", "state", "files_router", "cleanup"}
    for _m in list(sys.modules):
        if _m in _purge or _m.startswith("jobs.") or _m.startswith("tasks."):
            del sys.modules[_m]

    from config import get_settings
    get_settings.cache_clear()

    from main import app  # noqa: E402

    with TestClient(app) as c:
        yield c, media

    get_settings.cache_clear()


AUTH = {"X-Worker-Token": "test-token"}


def _wait_done(c: TestClient, job_id: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = c.get(f"/jobs/{job_id}", headers=AUTH)
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} not finished in {timeout}s")


def test_healthz(client):
    c, _ = client
    r = c.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["stub_mode"] is True
    assert body["fixture_present"] is True
    assert body["max_concurrent"] == 1


def test_download_requires_auth(client):
    c, _ = client
    r = c.post(
        "/jobs/download",
        json={"url": "https://www.instagram.com/reel/abc/", "platform": "instagram"},
    )
    assert r.status_code == 401


def test_download_stub_lifecycle(client):
    c, media = client

    r = c.post(
        "/jobs/download",
        json={
            "url": "https://www.instagram.com/reel/CXXXXXX/",
            "platform": "instagram",
            "quality": "720p",
        },
        headers=AUTH,
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    body = _wait_done(c, job_id)
    assert body["status"] == "done", body
    res = body["result"]
    assert res["strategy_used"] == "stub"
    assert res["platform"] == "instagram"  # echo из запроса
    assert res["sha256"] and len(res["sha256"]) == 64
    assert res["size_bytes"] > 0
    assert Path(res["file_path"]).exists()
    assert Path(res["file_path"]).is_relative_to(media / "downloads")


def test_delete_file_after_done(client):
    c, _ = client

    r = c.post(
        "/jobs/download",
        json={"url": "https://x.com/y", "platform": "tiktok"},
        headers=AUTH,
    )
    job_id = r.json()["job_id"]
    body = _wait_done(c, job_id)
    file_path = Path(body["result"]["file_path"])
    assert file_path.exists()

    r2 = c.delete(f"/files/{job_id}", headers=AUTH)
    assert r2.status_code == 204
    assert not file_path.exists()

    # Идемпотентно: повторный DELETE — тоже 204
    r3 = c.delete(f"/files/{job_id}", headers=AUTH)
    assert r3.status_code == 204


def test_validation_rejects_bad_platform(client):
    c, _ = client
    r = c.post(
        "/jobs/download",
        json={"url": "https://example.com/x", "platform": "facebook"},
        headers=AUTH,
    )
    assert r.status_code == 422


def test_validation_rejects_bad_url(client):
    c, _ = client
    r = c.post(
        "/jobs/download",
        json={"url": "not-a-url", "platform": "instagram"},
        headers=AUTH,
    )
    assert r.status_code == 422
