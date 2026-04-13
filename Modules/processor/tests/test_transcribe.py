"""Тесты транскрипции с моками провайдеров.

Реальные клиенты ходят в сеть — мы их патчим на фейковый класс,
чтобы проверить оркестрацию resolver + task + usage-логирование + fallback.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


FERNET = Fernet.generate_key().decode()
HAS_FFMPEG = shutil.which("ffmpeg") is not None


class _FakeClient:
    """Фейковый клиент транскрипции. Возвращает детерминированный текст."""

    def __init__(self, provider: str, *, should_fail: bool = False):
        self.provider = provider
        self.default_model = "fake-model"
        self.should_fail = should_fail
        self.call_count = 0

    async def transcribe(self, *, audio_path, api_key, language, model=None):
        self.call_count += 1
        if self.should_fail:
            from viral_llm.clients.base import ProviderError
            raise ProviderError(f"{self.provider}_simulated_failure")
        from viral_llm.clients.base import TranscriptResult
        return TranscriptResult(
            text=f"hello from {self.provider}",
            language=language or "en",
            provider=self.provider,
            model=model or self.default_model,
            duration_sec=3.0,
            latency_ms=42,
        )


def _make_tiny_mp4(path: Path) -> None:
    """Создаёт 1-секундное silent-видео 16x16 через ffmpeg."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=16x16:d=1",
            "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
            "-t", "1",
            "-shortest",
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
        if mod.startswith(("main", "config", "auth", "state", "jobs", "cache", "tasks", "api", "prompts")):
            sys.modules.pop(mod, None)

    from main import app  # noqa: E402
    import viral_llm.clients.registry as reg  # noqa: E402

    # Патчим все клиенты транскрипции на фейки
    fakes = {
        "assemblyai": _FakeClient("assemblyai"),
        "deepgram": _FakeClient("deepgram"),
        "openai_whisper": _FakeClient("openai_whisper"),
        "groq_whisper": _FakeClient("groq_whisper"),
    }
    monkeypatch.setattr(reg, "TRANSCRIPTION_CLIENTS", fakes)

    with TestClient(app) as c:
        yield c, media, fakes


ADMIN = {"X-Admin-Token": "test-admin"}
WORKER = {"X-Worker-Token": "test-worker"}


def _wait_done(c, job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = c.get(f"/jobs/{job_id}", headers=WORKER).json()
        if r["status"] in ("done", "failed"):
            return r
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not in PATH")
def test_transcribe_happy_path(client):
    c, media, fakes = client
    # добавим один ключ deepgram
    r = c.post(
        "/admin/api-keys",
        json={"provider": "deepgram", "secret": "dg-test", "label": "dg-main"},
        headers=ADMIN,
    )
    assert r.status_code == 201

    video = media / "downloads" / "tiny.mp4"
    _make_tiny_mp4(video)

    r = c.post(
        "/jobs/transcribe",
        json={"file_path": str(video), "language": "en"},
        headers=WORKER,
    )
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    done = _wait_done(c, job_id)
    assert done["status"] == "done", done
    res = done["result"]
    assert res["transcript"]["provider"] == "deepgram"
    assert res["transcript"]["text"] == "hello from deepgram"
    assert res["cost_usd"]["transcription"] >= 0


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not in PATH")
def test_transcribe_fallback_chain(client):
    c, media, fakes = client
    # deepgram падает (priority 1), assemblyai работает (priority 2)
    fakes["deepgram"].should_fail = True
    c.post(
        "/admin/api-keys",
        json={"provider": "deepgram", "secret": "dg", "priority": 1, "label": "dg"},
        headers=ADMIN,
    )
    c.post(
        "/admin/api-keys",
        json={"provider": "assemblyai", "secret": "aa", "priority": 2, "label": "aa"},
        headers=ADMIN,
    )

    video = media / "downloads" / "tiny2.mp4"
    _make_tiny_mp4(video)

    r = c.post("/jobs/transcribe", json={"file_path": str(video)}, headers=WORKER)
    job_id = r.json()["job_id"]
    done = _wait_done(c, job_id)

    assert done["status"] == "done"
    assert done["result"]["transcript"]["provider"] == "assemblyai"
    # deepgram был вызван 1 раз (провалился), assemblyai - 1 раз (успех)
    assert fakes["deepgram"].call_count == 1
    assert fakes["assemblyai"].call_count == 1

    # usage должен содержать обе записи
    usage = c.get("/admin/usage", headers=ADMIN).json()
    providers = {r["provider"] for r in usage["by_provider"]}
    assert "deepgram" in providers
    assert "assemblyai" in providers


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not in PATH")
def test_transcribe_no_active_key_fails(client):
    c, media, _ = client
    video = media / "downloads" / "tiny3.mp4"
    _make_tiny_mp4(video)

    r = c.post("/jobs/transcribe", json={"file_path": str(video)}, headers=WORKER)
    assert r.status_code == 202  # принято в очередь
    job_id = r.json()["job_id"]
    done = _wait_done(c, job_id)
    assert done["status"] == "failed"
    assert "no_active_keys" in done["error"].lower() or "noprovideravailable" in done["error"].lower()


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not in PATH")
def test_transcribe_explicit_provider(client):
    c, media, fakes = client
    c.post("/admin/api-keys", json={"provider": "deepgram", "secret": "dg", "priority": 1, "label": "dg"}, headers=ADMIN)
    c.post("/admin/api-keys", json={"provider": "groq_whisper", "secret": "gr", "priority": 5, "label": "gr"}, headers=ADMIN)

    video = media / "downloads" / "tiny4.mp4"
    _make_tiny_mp4(video)

    r = c.post(
        "/jobs/transcribe",
        json={"file_path": str(video), "provider": "groq_whisper"},
        headers=WORKER,
    )
    done = _wait_done(c, r.json()["job_id"])
    assert done["status"] == "done"
    assert done["result"]["transcript"]["provider"] == "groq_whisper"
    # deepgram не должен был быть вызван
    assert fakes["deepgram"].call_count == 0
    assert fakes["groq_whisper"].call_count == 1


def test_extract_audio_smoke(tmp_path):
    """Проверяем, что ffmpeg доступен и извлечение аудио работает."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not in PATH")
    import asyncio
    from tasks.extract_audio import extract_audio

    src = tmp_path / "tiny.mp4"
    _make_tiny_mp4(src)
    out = tmp_path / "out.mp3"
    result = asyncio.run(extract_audio(src, out))
    assert out.exists()
    assert out.stat().st_size > 0
    assert result.duration_sec > 0
