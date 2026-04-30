"""Тесты on-demand script-генерации ("Создать аналог"):
- POST /api/orchestrator/runs/{id}/scripts → 201, сохраняет script-meta в run.scripts[]
- 404 для несуществующего run-а
- 409 если run.status != 'done'
- 503 если script_service не настроен
- GET /api/orchestrator/runs/{id}/scripts → список созданных скриптов

Использует прямой доступ к state.run_store для seeding done-run-а
вместо прохождения через полный pipeline.
"""
import asyncio
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_script(tmp_path: Path, monkeypatch):
    """FastAPI с настроенным script-клиентом (мокнутым) и mock-downloader/processor."""
    db = tmp_path / "db"
    db.mkdir()

    monkeypatch.setenv("DB_DIR", str(db))
    monkeypatch.setenv("DOWNLOADER_URL", "http://fake-downloader")
    monkeypatch.setenv("DOWNLOADER_TOKEN", "test")
    monkeypatch.setenv("PROCESSOR_URL", "http://fake-processor")
    monkeypatch.setenv("PROCESSOR_TOKEN", "test")
    monkeypatch.setenv("SCRIPT_URL", "http://fake-script")  # ← включает script_client
    monkeypatch.setenv("SCRIPT_TOKEN", "test-script")
    monkeypatch.setenv("ORCHESTRATOR_RUN_TIMEOUT_SEC", "5")
    monkeypatch.setenv("ORCHESTRATOR_POLL_INTERVAL_SEC", "0.01")

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from orchestrator.clients import downloader as dlmod
    from orchestrator.clients import processor as procmod
    from orchestrator.clients import script as scriptmod
    from orchestrator.config import get_orchestrator_settings
    get_orchestrator_settings.cache_clear()

    # Минимальные моки чтобы lifespan стартовал
    async def fake_dl_submit(self, **kw): return "dl-x"
    async def fake_proc_submit(self, **kw): return "proc-x"

    monkeypatch.setattr(dlmod.DownloaderClient, "submit", fake_dl_submit)
    monkeypatch.setattr(procmod.ProcessorClient, "submit_transcribe", fake_proc_submit)
    monkeypatch.setattr(procmod.ProcessorClient, "submit_vision_analyze", fake_proc_submit)
    monkeypatch.setattr(procmod.ProcessorClient, "submit_analyze_strategy", fake_proc_submit)

    # Script client mock — настраиваем поведение через _fake_script_state
    fake_state: dict = {"calls": [], "fail": False, "response": None}

    async def fake_script_generate(self, *, template, params, profile):
        fake_state["calls"].append({"template": template, "params": params, "profile": profile})
        if fake_state["fail"]:
            raise scriptmod.ScriptError("simulated_failure")
        return fake_state["response"] or {
            "id": "script-uuid-123",
            "template": template,
            "status": "ok",
            "cost_usd": 0.05,
            "provider": "openai_text",
            "model": "gpt-4o",
            "body": {"hook": {"text": "Привет!"}, "body": [], "cta": {"text": "Подписывайся"}},
        }

    monkeypatch.setattr(scriptmod.ScriptClient, "generate", fake_script_generate)

    from main import app  # noqa: E402

    with TestClient(app) as c:
        c._fake_script_state = fake_state  # type: ignore[attr-defined]
        yield c

    get_orchestrator_settings.cache_clear()


def _seed_done_run(client: TestClient, *, transcript_text="Тестовый разбор видео",
                   duration=45, platform="instagram", account_id=None):
    """Создаёт run в БД напрямую с заданным состоянием (done)."""
    from orchestrator.state import state
    rid = state.run_store.create(
        url="https://example.com/r/abc",
        platform=platform,
        external_id="abc",
        account_id=account_id,
    )
    state.run_store.patch_step(rid, "download", {
        "status": "done", "duration_sec": duration, "file_path": "/media/x.mp4",
    })
    state.run_store.patch_step(rid, "transcribe", {
        "status": "done",
        "text": transcript_text,
        "language": "ru",
        "provider": "openai_whisper",
    })
    state.run_store.patch_step(rid, "vision", {
        "status": "done", "frames_count": 5,
    })
    state.run_store.set_status(rid, "done", result={"ok": True})
    return rid


# ============================================================
# POST /runs/{id}/scripts — happy path
# ============================================================

def test_create_script_returns_201_and_script_body(app_with_script):
    c = app_with_script
    rid = _seed_done_run(c)
    r = c.post(f"/api/orchestrator/runs/{rid}/scripts", json={})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] == "script-uuid-123"
    assert body["status"] == "ok"


def test_create_script_passes_transcript_as_topic(app_with_script):
    """topic должен браться из transcribe.text (truncate до 500 символов)."""
    c = app_with_script
    rid = _seed_done_run(c, transcript_text="Длинный текст " * 50)
    r = c.post(f"/api/orchestrator/runs/{rid}/scripts", json={})
    assert r.status_code == 201

    call = c._fake_script_state["calls"][0]
    assert len(call["params"]["topic"]) == 500
    assert call["params"]["topic"].startswith("Длинный текст")
    assert call["params"]["language"] == "ru"
    assert call["params"]["duration_sec"] == 45  # из download.duration_sec


def test_create_script_uses_platform_template_default(app_with_script):
    """instagram → reels_hook_v1 / reels; youtube_shorts → shorts_story_v1 / shorts."""
    c = app_with_script
    # Instagram
    rid_ig = _seed_done_run(c, platform="instagram")
    c.post(f"/api/orchestrator/runs/{rid_ig}/scripts", json={})
    # YT Shorts
    rid_yt = _seed_done_run(c, platform="youtube_shorts")
    c.post(f"/api/orchestrator/runs/{rid_yt}/scripts", json={})

    calls = c._fake_script_state["calls"]
    ig_call = next(x for x in calls if x["params"].get("format") == "reels")
    yt_call = next(x for x in calls if x["params"].get("format") == "shorts")
    assert ig_call["template"] == "reels_hook_v1"
    assert yt_call["template"] == "shorts_story_v1"


def test_create_script_with_user_overrides(app_with_script):
    """Пользователь может перекрыть template/tone/pattern_hint."""
    c = app_with_script
    rid = _seed_done_run(c)
    r = c.post(
        f"/api/orchestrator/runs/{rid}/scripts",
        json={"template": "long", "tone": "ironic", "pattern_hint": "PASTOR"},
    )
    assert r.status_code == 201
    call = c._fake_script_state["calls"][0]
    assert call["template"] == "long"
    assert call["params"]["tone"] == "ironic"
    assert call["params"]["pattern_hint"] == "PASTOR"


def test_create_script_appends_to_run_scripts_list(app_with_script):
    """После создания скрипта он появляется в GET /runs/{id} и /runs/{id}/scripts.
    Override (template) = намеренно новый вариант → не дедупится.
    """
    c = app_with_script
    rid = _seed_done_run(c)
    c.post(f"/api/orchestrator/runs/{rid}/scripts", json={})
    c.post(f"/api/orchestrator/runs/{rid}/scripts", json={"template": "long"})

    r = c.get(f"/api/orchestrator/runs/{rid}/scripts")
    assert r.status_code == 200
    scripts = r.json()
    assert len(scripts) == 2
    assert scripts[0]["id"] == "script-uuid-123"
    assert scripts[0]["status"] == "ok"
    assert scripts[0]["cost_usd"] == 0.05

    # Также видны в самом run
    run = c.get(f"/api/orchestrator/runs/{rid}").json()
    assert len(run["scripts"]) == 2


# ============================================================
# Error paths
# ============================================================

def test_create_script_404_for_unknown_run(app_with_script):
    c = app_with_script
    r = c.post("/api/orchestrator/runs/nonexistent/scripts", json={})
    assert r.status_code == 404
    assert r.json()["detail"] == "run_not_found"


def test_create_script_409_when_run_not_done(app_with_script):
    c = app_with_script
    from orchestrator.state import state
    rid = state.run_store.create(url="https://x", platform="instagram")
    # Не set_status → run в queued
    r = c.post(f"/api/orchestrator/runs/{rid}/scripts", json={})
    assert r.status_code == 409
    assert "run_not_done" in r.json()["detail"]


def test_create_script_409_when_no_transcript(app_with_script):
    """Если transcribe.text пустой — нечего использовать как topic."""
    c = app_with_script
    rid = _seed_done_run(c, transcript_text="")
    r = c.post(f"/api/orchestrator/runs/{rid}/scripts", json={})
    assert r.status_code == 409
    assert r.json()["detail"] == "transcript_unavailable"


def test_create_script_502_when_script_service_fails(app_with_script):
    c = app_with_script
    c._fake_script_state["fail"] = True
    rid = _seed_done_run(c)
    r = c.post(f"/api/orchestrator/runs/{rid}/scripts", json={})
    assert r.status_code == 502
    assert "script_service_failed" in r.json()["detail"]


def test_idempotency_returns_existing_script_without_overrides(app_with_script):
    """Двойной POST без override → возвращается тот же script, не создаётся новый."""
    c = app_with_script
    rid = _seed_done_run(c)
    r1 = c.post(f"/api/orchestrator/runs/{rid}/scripts", json={})
    assert r1.status_code == 201
    assert r1.json().get("deduped") is not True

    r2 = c.post(f"/api/orchestrator/runs/{rid}/scripts", json={})
    assert r2.status_code == 201
    assert r2.json().get("deduped") is True

    scripts = c.get(f"/api/orchestrator/runs/{rid}/scripts").json()
    assert len(scripts) == 1, "idempotent — должен быть только один скрипт"


def test_list_scripts_empty_for_run_without_scripts(app_with_script):
    c = app_with_script
    rid = _seed_done_run(c)
    r = c.get(f"/api/orchestrator/runs/{rid}/scripts")
    assert r.status_code == 200
    assert r.json() == []


def test_list_scripts_404_for_unknown_run(app_with_script):
    c = app_with_script
    r = c.get("/api/orchestrator/runs/nonexistent/scripts")
    assert r.status_code == 404


# ============================================================
# Without script_url → 503
# ============================================================

@pytest.fixture
def app_no_script(tmp_path: Path, monkeypatch):
    """Версия без SCRIPT_URL → script_client=None."""
    db = tmp_path / "db"
    db.mkdir()
    monkeypatch.setenv("DB_DIR", str(db))
    monkeypatch.setenv("DOWNLOADER_URL", "http://fake-downloader")
    monkeypatch.setenv("DOWNLOADER_TOKEN", "test")
    monkeypatch.setenv("PROCESSOR_URL", "http://fake-processor")
    monkeypatch.setenv("PROCESSOR_TOKEN", "test")
    monkeypatch.setenv("SCRIPT_URL", "")  # пусто → script_client=None
    monkeypatch.setenv("ORCHESTRATOR_RUN_TIMEOUT_SEC", "5")

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from orchestrator.config import get_orchestrator_settings
    get_orchestrator_settings.cache_clear()

    from main import app
    with TestClient(app) as c:
        yield c
    get_orchestrator_settings.cache_clear()


def test_create_script_503_when_no_script_url(app_no_script):
    c = app_no_script
    from orchestrator.state import state
    rid = state.run_store.create(url="https://x", platform="instagram")
    state.run_store.patch_step(rid, "transcribe", {"status": "done", "text": "hi"})
    state.run_store.set_status(rid, "done")

    r = c.post(f"/api/orchestrator/runs/{rid}/scripts", json={})
    assert r.status_code == 503
    assert r.json()["detail"] == "script_service_not_configured"
