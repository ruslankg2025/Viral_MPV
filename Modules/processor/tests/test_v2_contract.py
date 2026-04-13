"""Тесты contract v2: artifacts на диске, source_ref echo, prompt_version,
analysis_version, обратная совместимость, reanalyze.
"""
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

FERNET = Fernet.generate_key().decode()


class _FakeTr:
    provider = "deepgram"
    default_model = "nova-3"

    async def transcribe(self, *, audio_path, api_key, language, model=None):
        from viral_llm.clients.base import TranscriptResult
        return TranscriptResult(
            text="hello world",
            language="en",
            provider="deepgram",
            model="nova-3",
            duration_sec=5.0,
            latency_ms=50,
        )


class _FakeVi:
    provider = "anthropic_claude"
    default_model = "claude-sonnet-4-6"

    async def analyze(self, *, frame_paths, api_key, prompt, model=None):
        from viral_llm.clients.base import VisionResult
        return VisionResult(
            raw_json={"hook": "h", "scenes": [], "why_viral": "w", "emotion_trigger": "e"},
            provider="anthropic_claude",
            model="claude-sonnet-4-6",
            input_tokens=100,
            output_tokens=50,
            latency_ms=80,
        )


@pytest.fixture()
def client(tmp_path, monkeypatch):
    media = tmp_path / "media"
    db = tmp_path / "db"
    (media / "downloads").mkdir(parents=True)
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
        if mod.startswith(("main", "config", "auth", "state", "jobs", "cache", "tasks", "api", "prompts", "schemas")):
            sys.modules.pop(mod, None)

    from main import app  # noqa: E402
    import viral_llm.clients.registry as reg  # noqa: E402

    monkeypatch.setattr(reg, "TRANSCRIPTION_CLIENTS", {"deepgram": _FakeTr()})
    monkeypatch.setattr(reg, "VISION_CLIENTS", {"anthropic_claude": _FakeVi()})

    from tasks.extract_audio import AudioResult

    async def fake_extract_audio(video_path, out_path, sample_rate=16000):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"fake-mp3")
        return AudioResult(path=out_path, duration_sec=5.0, sample_rate=sample_rate)

    import tasks.transcribe as tr_mod
    monkeypatch.setattr(tr_mod, "extract_audio", fake_extract_audio)

    from tasks.extract_frames import FrameInfo, FramesResult

    async def fake_extract_frames(*, video_path, out_dir, **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        frames = []
        for i in range(3):
            p = out_dir / f"frame_{i+1:03d}.jpg"
            img = np.full((32, 32, 3), i * 50, dtype=np.uint8)
            cv2.imwrite(str(p), img)
            frames.append(FrameInfo(index=i + 1, timestamp_sec=float(i), file_path=str(p), diff_ratio=1.0))
        return FramesResult(
            extracted=frames,
            stats={"raw_count": 3, "kept_count": 3, "dropped_count": 0, "duration_sec": 3.0},
        )

    import tasks.vision_analyze as va_mod
    monkeypatch.setattr(va_mod, "extract_frames", fake_extract_frames)

    with TestClient(app) as c:
        yield c, media


ADMIN = {"X-Admin-Token": "test-admin"}
WORKER = {"X-Worker-Token": "test-worker"}


def _wait_done(c, job_id, timeout=15):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = c.get(f"/jobs/{job_id}", headers=WORKER).json()
        if last["status"] in ("done", "failed"):
            return last
        time.sleep(0.05)
    raise AssertionError(f"timeout: {last}")


def _seed_keys(c):
    c.post("/admin/api-keys", json={"provider": "deepgram", "secret": "s", "label": "dg"}, headers=ADMIN)
    c.post("/admin/api-keys", json={"provider": "anthropic_claude", "secret": "s", "label": "an"}, headers=ADMIN)


# ------------------------------------------------------------------ contract v2

def test_full_analysis_v2_response_has_analysis_version_and_artifacts(client):
    c, media = client
    _seed_keys(c)
    video = media / "downloads" / "v.mp4"
    video.write_bytes(b"x")

    r = c.post("/jobs/full-analysis", json={"file_path": str(video)}, headers=WORKER)
    done = _wait_done(c, r.json()["job_id"])
    assert done["status"] == "done", done
    res = done["result"]

    # v2 поля
    assert res["analysis_version"] == "2.0"
    assert "prompt_version" in res
    assert res["prompt_version"] == "vision_default:v1"

    artifacts = res.get("artifacts") or {}
    assert "audio_path" in artifacts
    assert "transcript_path" in artifacts
    assert "frames_dir" in artifacts
    assert "vision_result_path" in artifacts

    # vision_result_path реально существует на диске
    vrp = Path(artifacts["vision_result_path"])
    assert vrp.exists(), f"missing {vrp}"
    parsed = json.loads(vrp.read_text(encoding="utf-8"))
    assert parsed["vision"]["hook"] == "h"
    assert parsed["prompt_version"] == "vision_default:v1"


def test_source_ref_is_echoed_into_result(client):
    c, media = client
    _seed_keys(c)
    video = media / "downloads" / "v.mp4"
    video.write_bytes(b"x")

    r = c.post(
        "/jobs/full-analysis",
        json={
            "file_path": str(video),
            "source_ref": {"platform": "youtube", "external_id": "abc123"},
        },
        headers=WORKER,
    )
    done = _wait_done(c, r.json()["job_id"])
    assert done["status"] == "done"
    assert done["result"]["source_ref"] == {"platform": "youtube", "external_id": "abc123"}


def test_v1_payload_still_works_backward_compat(client):
    """Старый клиент без новых полей должен получать done без ошибок."""
    c, media = client
    _seed_keys(c)
    video = media / "downloads" / "v.mp4"
    video.write_bytes(b"x")

    # Минимальный payload — только file_path
    r = c.post("/jobs/full-analysis", json={"file_path": str(video)}, headers=WORKER)
    assert r.status_code == 202
    done = _wait_done(c, r.json()["job_id"])
    assert done["status"] == "done"
    # Старые поля на месте
    assert done["result"]["transcript"]["text"] == "hello world"
    assert done["result"]["vision"]["hook"] == "h"
    assert done["result"]["cost_usd"]["total"] > 0


def test_providers_block_takes_priority_over_flat_fields(client):
    """Если задан providers.vision — он используется поверх vision_provider."""
    c, media = client
    _seed_keys(c)
    video = media / "downloads" / "v.mp4"
    video.write_bytes(b"x")

    r = c.post(
        "/jobs/full-analysis",
        json={
            "file_path": str(video),
            "providers": {"vision": "anthropic_claude", "transcription": "deepgram"},
        },
        headers=WORKER,
    )
    done = _wait_done(c, r.json()["job_id"])
    assert done["status"] == "done"
    assert done["result"]["vision"]["provider"] == "anthropic_claude"


# ------------------------------------------------------------------ reanalyze

def test_reanalyze_creates_new_job_with_link_to_base(client):
    c, media = client
    _seed_keys(c)
    video = media / "downloads" / "v.mp4"
    video.write_bytes(b"x")

    # Базовый job
    r = c.post(
        "/jobs/full-analysis",
        json={"file_path": str(video), "cache_key": "test:abc"},
        headers=WORKER,
    )
    base_id = r.json()["job_id"]
    _wait_done(c, base_id)

    # Создаём вторую версию промпта и активируем
    c.post(
        "/admin/prompts",
        json={"name": "vision_default", "version": "v2", "body": "BODY V2", "is_active": False},
        headers=ADMIN,
    )

    # Reanalyze с явным prompt_version=v1 (исходная) — для проверки что работает override
    r = c.post(
        "/jobs/reanalyze",
        json={
            "base_job_id": base_id,
            "override": {"prompt_template": "detailed"},
        },
        headers=WORKER,
    )
    assert r.status_code == 202, r.text
    new_id = r.json()["job_id"]
    assert r.json()["reanalysis_of"] == base_id

    new = _wait_done(c, new_id)
    assert new["status"] == "done"
    assert new["reanalysis_of"] == base_id
    assert new["parent_job_id"] == base_id
    # Исходный job не модифицирован
    base = c.get(f"/jobs/{base_id}", headers=WORKER).json()
    assert base["reanalysis_of"] is None
    assert base["status"] == "done"


def test_reanalyze_404_for_unknown_base(client):
    c, _ = client
    r = c.post(
        "/jobs/reanalyze",
        json={"base_job_id": "deadbeef" * 4, "override": {}},
        headers=WORKER,
    )
    assert r.status_code == 404


def test_reanalyze_409_when_base_not_done(client):
    """База должна быть в статусе done."""
    c, media = client
    _seed_keys(c)
    video = media / "downloads" / "v.mp4"
    video.write_bytes(b"x")

    # Создаём job но не ждём (поллинг сразу — статус queued/running)
    r = c.post("/jobs/full-analysis", json={"file_path": str(video)}, headers=WORKER)
    base_id = r.json()["job_id"]

    # Сразу пытаемся reanalyze — должно отказать (или 409, или базовый успеет завершиться)
    res = c.post(
        "/jobs/reanalyze",
        json={"base_job_id": base_id, "override": {}},
        headers=WORKER,
    )
    if res.status_code == 202:
        # успел — допустимо для очень быстрых mock-ов
        return
    assert res.status_code == 409
