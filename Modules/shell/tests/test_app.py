"""Smoke-тест целостного FastAPI app (lifespan + router + dedup).

Внешние HTTP к downloader/processor мокируются через monkeypatch.
"""
import asyncio
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_mocked_clients(tmp_path: Path, monkeypatch):
    """Подменяет downloader/processor клиенты до создания TestClient.

    Не чистим sys.modules (это ломало class identity для DownloaderError
    при последующих тестах).
    """
    db = tmp_path / "db"
    db.mkdir()

    monkeypatch.setenv("DB_DIR", str(db))
    monkeypatch.setenv("DOWNLOADER_URL", "http://fake-downloader")
    monkeypatch.setenv("DOWNLOADER_TOKEN", "test")
    monkeypatch.setenv("PROCESSOR_URL", "http://fake-processor")
    monkeypatch.setenv("PROCESSOR_TOKEN", "test")
    monkeypatch.setenv("ORCHESTRATOR_RUN_TIMEOUT_SEC", "5")
    monkeypatch.setenv("ORCHESTRATOR_POLL_INTERVAL_SEC", "0.01")

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from orchestrator.clients import downloader as dlmod
    from orchestrator.clients import processor as procmod
    from orchestrator.config import get_orchestrator_settings

    # lru_cache мог закешировать старые env — сбрасываем
    get_orchestrator_settings.cache_clear()

    async def fake_dl_submit(self, **kw):
        return "dl-job-x"

    async def fake_dl_wait(self, job_id, *, timeout_sec=300):
        return {
            "status": "done",
            "result": {
                "file_path": "/media/downloads/stub_test.mp4",
                "sha256": "a" * 64,
                "size_bytes": 1024,
                "duration_sec": 10.0,
                "strategy_used": "stub",
            },
        }

    async def fake_dl_delete(self, job_id):
        return None

    async def fake_proc_submit_transcribe(self, **kw):
        return "tr-job-x"

    async def fake_proc_submit_vision(self, **kw):
        return "vi-job-x"

    async def fake_proc_submit_strategy(self, **kw):
        return "st-job-x"

    async def fake_proc_wait(self, job_id, *, timeout_sec=300):
        await asyncio.sleep(0.3)
        if job_id == "tr-job-x":
            return {
                "status": "done",
                "result": {
                    "transcript": {
                        "text": "test transcript",
                        "language": "en",
                        "provider": "openai_whisper",
                        "model": "whisper-1",
                        "duration_sec": 10.0,
                        "segments": [{"start": 0.0, "end": 1.0, "text": "test"}],
                    },
                    "cost_usd": {"transcription": 0.01},
                },
            }
        if job_id == "vi-job-x":
            return {
                "status": "done",
                "result": {
                    "frames": {
                        "extracted": [
                            {"index": 1, "timestamp_sec": 0.0,
                             "file_path": "/media/frames/test/frame_001.jpg",
                             "diff_ratio": 1.0},
                        ],
                        "stats": {"raw_count": 1, "kept_count": 1},
                    },
                    "vision": {
                        "provider": "anthropic_claude",
                        "model": "claude-sonnet-4-6",
                        "prompt_version": "vision_default:v1",
                    },
                    "cost_usd": {"vision": 0.01},
                },
            }
        # st-job-x → strategy
        return {
            "status": "done",
            "result": {
                "sections": [
                    {"id": "why", "title": "Почему", "body": "стаб"},
                    {"id": "audience", "title": "ЦА", "body": "стаб"},
                    {"id": "triggers", "title": "Триггеры", "body": "стаб"},
                    {"id": "windows", "title": "Окна", "body": "стаб"},
                    {"id": "recipe", "title": "Рецепт", "body": "стаб"},
                ],
                "provider": "anthropic_claude_text",
                "model": "claude-sonnet-4-6",
                "prompt_version": "strategy_v1",
                "cost_usd": {"strategy": 0.04},
            },
        }

    monkeypatch.setattr(dlmod.DownloaderClient, "submit", fake_dl_submit)
    monkeypatch.setattr(dlmod.DownloaderClient, "wait_done", fake_dl_wait)
    monkeypatch.setattr(dlmod.DownloaderClient, "delete_file", fake_dl_delete)
    monkeypatch.setattr(procmod.ProcessorClient, "submit_transcribe", fake_proc_submit_transcribe)
    monkeypatch.setattr(procmod.ProcessorClient, "submit_vision_analyze", fake_proc_submit_vision)
    monkeypatch.setattr(procmod.ProcessorClient, "submit_analyze_strategy", fake_proc_submit_strategy)
    monkeypatch.setattr(procmod.ProcessorClient, "wait_done", fake_proc_wait)

    # Monitor client: возвращает фейковый видео-meta для test_create_run_from_video_id.
    # Тест может переопределить _fake_monitor_videos[video_id].
    from orchestrator.clients import monitor as monmod
    _fake_monitor_videos: dict = {
        "vid-from-monitor": {
            "id": "vid-from-monitor",
            "url": "https://www.instagram.com/reel/CMON/",
            "platform": "instagram",
            "external_id": "CMON",
        }
    }

    async def fake_get_video(self, video_id):
        v = _fake_monitor_videos.get(video_id)
        if v is None:
            raise monmod.MonitorError(f"video_not_found: {video_id}")
        return v

    async def fake_patch_analysis(self, video_id, **kw):
        return {"id": video_id, **kw}

    monkeypatch.setattr(monmod.MonitorClient, "get_video", fake_get_video)
    monkeypatch.setattr(monmod.MonitorClient, "patch_analysis", fake_patch_analysis)

    from main import app  # noqa: E402

    with TestClient(app) as c:
        yield c

    # Сброс кеша после теста, чтобы следующий тест получил чистое состояние
    get_orchestrator_settings.cache_clear()


def _wait_run_terminal(c: TestClient, run_id: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = c.get(f"/api/orchestrator/runs/{run_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish")


def test_create_run_and_complete(app_with_mocked_clients):
    c = app_with_mocked_clients
    r = c.post(
        "/api/orchestrator/runs",
        json={
            "url": "https://www.instagram.com/reel/CABC/",
            "platform": "instagram",
            "external_id": "CABC",
        },
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["pipeline"] == ["download", "analyze"]
    run_id = body["run_id"]

    final = _wait_run_terminal(c, run_id)
    assert final["status"] == "done"
    assert final["steps"]["download"]["status"] == "done"
    assert final["steps"]["transcribe"]["status"] == "done"
    assert final["steps"]["vision"]["status"] == "done"
    assert final["steps"]["strategy"]["status"] == "done"
    assert final["result"]["transcribe"]["cost_usd"] == 0.01
    assert final["result"]["vision"]["cost_usd"] == 0.01
    assert final["result"]["strategy"]["cost_usd"] == 0.04
    assert len(final["steps"]["strategy"]["sections"]) == 5


def test_single_flight_returns_existing(app_with_mocked_clients):
    c = app_with_mocked_clients
    payload = {
        "url": "https://www.instagram.com/reel/CDEF/",
        "platform": "instagram",
        "video_id": "vid-dedup",
    }
    r1 = c.post("/api/orchestrator/runs", json=payload)
    assert r1.status_code == 202
    run1 = r1.json()["run_id"]

    # Сразу же повторный запрос — ДОЛЖЕН вернуть тот же run_id с deduped=true
    r2 = c.post("/api/orchestrator/runs", json=payload)
    assert r2.status_code == 202
    body2 = r2.json()
    assert body2["run_id"] == run1
    assert body2.get("deduped") is True


def test_get_run_404(app_with_mocked_clients):
    c = app_with_mocked_clients
    r = c.get("/api/orchestrator/runs/nonexistent")
    assert r.status_code == 404


def test_list_runs(app_with_mocked_clients):
    c = app_with_mocked_clients
    c.post(
        "/api/orchestrator/runs",
        json={"url": "https://x.com/1", "platform": "tiktok"},
    )
    r = c.get("/api/orchestrator/runs")
    assert r.status_code == 200
    runs = r.json()
    assert len(runs) >= 1


def test_validation_bad_platform(app_with_mocked_clients):
    c = app_with_mocked_clients
    r = c.post(
        "/api/orchestrator/runs",
        json={"url": "https://x", "platform": "facebook"},
    )
    assert r.status_code == 422


def test_validation_bad_url(app_with_mocked_clients):
    c = app_with_mocked_clients
    r = c.post(
        "/api/orchestrator/runs",
        json={"url": "not-a-url", "platform": "instagram"},
    )
    assert r.status_code == 422


# ---------------- Этап 3: lookup-by-video_id ----------------

def test_create_run_from_video_id_uses_monitor(app_with_mocked_clients):
    c = app_with_mocked_clients
    # video_id="vid-from-monitor" зашит в фикстуру _fake_monitor_videos
    r = c.post(
        "/api/orchestrator/runs",
        json={"video_id": "vid-from-monitor"},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    run_id = body["run_id"]

    # Run должен подтянуть url/platform из monitor
    r2 = c.get(f"/api/orchestrator/runs/{run_id}")
    run = r2.json()
    assert run["url"] == "https://www.instagram.com/reel/CMON/"
    assert run["platform"] == "instagram"
    assert run["external_id"] == "CMON"
    # video_meta закэшировано в run-е
    assert run["video_meta"] is not None
    assert run["video_meta"]["id"] == "vid-from-monitor"


def test_create_run_video_id_not_in_monitor_returns_404(app_with_mocked_clients):
    c = app_with_mocked_clients
    r = c.post(
        "/api/orchestrator/runs",
        json={"video_id": "totally-unknown"},
    )
    assert r.status_code == 404
    assert "monitor_lookup_failed" in r.json()["detail"]


def test_create_run_no_inputs_400(app_with_mocked_clients):
    c = app_with_mocked_clients
    r = c.post("/api/orchestrator/runs", json={})
    assert r.status_code == 400
    assert "missing_inputs" in r.json()["detail"]
