"""Vision tests с моками провайдеров + JSON-парсером."""
import asyncio
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


class _FakeVisionClient:
    def __init__(self, provider: str, *, model: str = "fake-m", should_fail: bool = False):
        self.provider = provider
        self.default_model = model
        self.should_fail = should_fail
        self.call_count = 0

    async def analyze(self, *, frame_paths, api_key, prompt, model=None):
        self.call_count += 1
        if self.should_fail:
            from viral_llm.clients.base import ProviderError
            raise ProviderError(f"{self.provider}_fake_fail")
        from viral_llm.clients.base import VisionResult
        return VisionResult(
            raw_json={
                "hook": "fake hook",
                "structure": "fake structure",
                "scenes": [{"timestamp_sec": 0.0, "summary": "scene 1"}],
                "why_viral": "fake reason",
                "emotion_trigger": "surprise",
            },
            provider=self.provider,
            model=model or self.default_model,
            input_tokens=500,
            output_tokens=200,
            latency_ms=100,
        )


def _make_test_image(path: Path, color: tuple[int, int, int] = (10, 20, 30)) -> None:
    img = np.full((64, 64, 3), color, dtype=np.uint8)
    cv2.imwrite(str(path), img)


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

    fakes = {
        "anthropic_claude": _FakeVisionClient("anthropic_claude"),
        "openai_gpt4o": _FakeVisionClient("openai_gpt4o"),
        "openai_gpt4o_mini": _FakeVisionClient("openai_gpt4o_mini"),
        "google_gemini_pro": _FakeVisionClient("google_gemini_pro"),
        "google_gemini_flash": _FakeVisionClient("google_gemini_flash"),
    }
    monkeypatch.setattr(reg, "VISION_CLIENTS", fakes)

    # Подменим extract_frames — детерминированный набор кадров без ffmpeg
    async def fake_extract_frames(*, video_path, out_dir, **kwargs):
        from tasks.extract_frames import FrameInfo, FramesResult
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for i, color in enumerate([(10, 20, 30), (200, 100, 50), (0, 255, 0)], start=1):
            p = out_dir / f"frame_{i:03d}.jpg"
            _make_test_image(p, color=color)
            paths.append(
                FrameInfo(
                    index=i,
                    timestamp_sec=float(i),
                    file_path=str(p),
                    diff_ratio=1.0 if i == 1 else 0.5,
                )
            )
        return FramesResult(
            extracted=paths,
            stats={
                "raw_count": 3,
                "kept_count": 3,
                "dropped_count": 0,
                "duration_sec": 3.0,
                "fps": 1,
                "diff_threshold": 0.1,
                "min_frames": 3,
                "max_frames": 40,
            },
        )

    import tasks.vision_analyze as va
    monkeypatch.setattr(va, "extract_frames", fake_extract_frames)

    with TestClient(app) as c:
        yield c, media, fakes


ADMIN = {"X-Admin-Token": "test-admin"}
WORKER = {"X-Worker-Token": "test-worker"}


def _wait_done(c, job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = c.get(f"/jobs/{job_id}", headers=WORKER).json()
        if r["status"] in ("done", "failed"):
            return r
        time.sleep(0.05)
    raise AssertionError("timeout")


def test_json_extractor_parses_plain():
    from viral_llm.clients.anthropic_claude import _extract_json
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_json_extractor_parses_markdown_block():
    from viral_llm.clients.anthropic_claude import _extract_json
    text = 'here is my answer:\n```json\n{"x": "y"}\n```\n'
    assert _extract_json(text) == {"x": "y"}


def test_json_extractor_parses_loose():
    from viral_llm.clients.anthropic_claude import _extract_json
    text = 'preamble {"hook": "abc", "scenes": []} trailing'
    assert _extract_json(text)["hook"] == "abc"


def test_json_extractor_fails_on_garbage():
    from viral_llm.clients.anthropic_claude import _extract_json
    from viral_llm.clients.base import ProviderError
    with pytest.raises(ProviderError):
        _extract_json("not json at all")


def test_vision_happy_path(client):
    c, media, fakes = client
    c.post(
        "/admin/api-keys",
        json={"provider": "anthropic_claude", "secret": "sk-ant-x", "label": "an-main"},
        headers=ADMIN,
    )
    video = media / "downloads" / "v1.mp4"
    video.write_bytes(b"dummy")  # file_path валидатор только проверяет существование

    r = c.post(
        "/jobs/vision-analyze",
        json={"file_path": str(video)},
        headers=WORKER,
    )
    assert r.status_code == 202
    done = _wait_done(c, r.json()["job_id"])
    assert done["status"] == "done", done
    res = done["result"]
    assert res["vision"]["provider"] == "anthropic_claude"
    assert res["vision"]["hook"] == "fake hook"
    assert res["cost_usd"]["vision"] > 0
    assert len(res["frames"]["extracted"]) == 3


def test_vision_fallback(client):
    c, media, fakes = client
    fakes["anthropic_claude"].should_fail = True
    c.post(
        "/admin/api-keys",
        json={"provider": "anthropic_claude", "secret": "x", "priority": 1, "label": "an"},
        headers=ADMIN,
    )
    c.post(
        "/admin/api-keys",
        json={"provider": "openai_gpt4o", "secret": "y", "priority": 2, "label": "oa"},
        headers=ADMIN,
    )

    video = media / "downloads" / "v2.mp4"
    video.write_bytes(b"dummy")

    r = c.post("/jobs/vision-analyze", json={"file_path": str(video)}, headers=WORKER)
    done = _wait_done(c, r.json()["job_id"])
    assert done["status"] == "done"
    assert done["result"]["vision"]["provider"] == "openai_gpt4o"
    assert fakes["anthropic_claude"].call_count == 1
    assert fakes["openai_gpt4o"].call_count == 1


def test_vision_no_keys_fails(client):
    c, media, _ = client
    video = media / "downloads" / "v3.mp4"
    video.write_bytes(b"dummy")
    r = c.post("/jobs/vision-analyze", json={"file_path": str(video)}, headers=WORKER)
    done = _wait_done(c, r.json()["job_id"])
    assert done["status"] == "failed"


def test_vision_explicit_provider(client):
    c, media, fakes = client
    c.post("/admin/api-keys", json={"provider": "anthropic_claude", "secret": "a", "priority": 1, "label": "a"}, headers=ADMIN)
    c.post("/admin/api-keys", json={"provider": "google_gemini_pro", "secret": "g", "priority": 5, "label": "g"}, headers=ADMIN)

    video = media / "downloads" / "v4.mp4"
    video.write_bytes(b"dummy")
    r = c.post(
        "/jobs/vision-analyze",
        json={"file_path": str(video), "provider": "google_gemini_pro"},
        headers=WORKER,
    )
    done = _wait_done(c, r.json()["job_id"])
    assert done["status"] == "done"
    assert done["result"]["vision"]["provider"] == "google_gemini_pro"
    assert fakes["anthropic_claude"].call_count == 0
    assert fakes["google_gemini_pro"].call_count == 1
