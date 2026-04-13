"""Full analysis orchestrator tests — параллельный запуск transcribe и vision."""
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
    # Устанавливаем в "" (не pop) чтобы перезаписать значения из .env.processor,
    # иначе pydantic-settings подтянет реальные ключи из файла и ломает тесты.
    for k in [
        "BOOTSTRAP_ASSEMBLYAI_API_KEY",
        "BOOTSTRAP_DEEPGRAM_API_KEY",
        "BOOTSTRAP_OPENAI_WHISPER_API_KEY",
        "BOOTSTRAP_GROQ_API_KEY",
        "BOOTSTRAP_ANTHROPIC_API_KEY",
        "BOOTSTRAP_OPENAI_API_KEY",
        "BOOTSTRAP_GOOGLE_GEMINI_API_KEY",
    ]:
        os.environ[k] = ""

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "auth", "state", "jobs", "cache", "tasks", "api", "prompts")):
            sys.modules.pop(mod, None)

    from main import app  # noqa: E402
    import viral_llm.clients.registry as reg  # noqa: E402

    monkeypatch.setattr(reg, "TRANSCRIPTION_CLIENTS", {"deepgram": _FakeTr()})
    monkeypatch.setattr(reg, "VISION_CLIENTS", {"anthropic_claude": _FakeVi()})

    # Фейки для audio/frames, чтобы ffmpeg был не нужен
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
    while time.time() < deadline:
        r = c.get(f"/jobs/{job_id}", headers=WORKER).json()
        if r["status"] in ("done", "failed"):
            return r
        time.sleep(0.05)
    raise AssertionError(f"timeout: {r}")


def test_full_analysis_happy_path(client):
    c, media = client
    c.post(
        "/admin/api-keys",
        json={"provider": "deepgram", "secret": "s", "label": "dg"},
        headers=ADMIN,
    )
    c.post(
        "/admin/api-keys",
        json={"provider": "anthropic_claude", "secret": "s", "label": "an"},
        headers=ADMIN,
    )

    video = media / "downloads" / "v.mp4"
    video.write_bytes(b"x")

    r = c.post("/jobs/full-analysis", json={"file_path": str(video)}, headers=WORKER)
    assert r.status_code == 202
    done = _wait_done(c, r.json()["job_id"])
    assert done["status"] == "done", done
    res = done["result"]
    assert res["transcript"]["text"] == "hello world"
    assert res["vision"]["hook"] == "h"
    assert res["cost_usd"]["total"] > 0
    assert "transcription" in res["cost_usd"]
    assert "vision" in res["cost_usd"]


def test_full_analysis_transcribe_fails_vision_ok(client):
    c, media = client
    # только vision ключ
    c.post(
        "/admin/api-keys",
        json={"provider": "anthropic_claude", "secret": "s", "label": "an"},
        headers=ADMIN,
    )
    video = media / "downloads" / "v2.mp4"
    video.write_bytes(b"x")

    r = c.post("/jobs/full-analysis", json={"file_path": str(video)}, headers=WORKER)
    done = _wait_done(c, r.json()["job_id"])
    assert done["status"] == "done", done
    res = done["result"]
    assert "transcript_error" in res
    assert res["vision"]["hook"] == "h"


def test_full_analysis_both_fail(client):
    c, media = client
    # Нет ключей вообще
    video = media / "downloads" / "v3.mp4"
    video.write_bytes(b"x")

    r = c.post("/jobs/full-analysis", json={"file_path": str(video)}, headers=WORKER)
    done = _wait_done(c, r.json()["job_id"])
    assert done["status"] == "failed"
