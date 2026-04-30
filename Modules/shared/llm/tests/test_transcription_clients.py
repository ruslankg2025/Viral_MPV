"""Тесты transcription-клиентов: проверяем что segments[] корректно парсятся
из ответа провайдера (verbose_json для Whisper, utterances для Deepgram/AssemblyAI).
"""
import asyncio
from pathlib import Path

import httpx
import pytest

from viral_llm.clients.assemblyai import AssemblyAIClient
from viral_llm.clients.base import ProviderError, TranscriptResult
from viral_llm.clients.deepgram import DeepgramClient
from viral_llm.clients.groq_whisper import GroqWhisperClient
from viral_llm.clients.openai_whisper import OpenAIWhisperClient


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)[:500]

    def json(self):
        return self._payload


@pytest.fixture
def fake_audio(tmp_path: Path) -> Path:
    """Минимальный mp3-файл (контент не важен — клиенты не парсят аудио)."""
    p = tmp_path / "test.mp3"
    p.write_bytes(b"\xff\xfb\x90\x00fake-mp3")
    return p


# ============================================================
# TranscriptResult dataclass: дефолт segments=[]
# ============================================================

def test_transcript_result_segments_default_empty():
    r = TranscriptResult(
        text="hi", language="en", provider="x", model="y",
        duration_sec=1.0, latency_ms=100,
    )
    assert r.segments == []


# ============================================================
# OpenAI Whisper — verbose_json возвращает segments[]
# ============================================================

def _patch_openai_whisper(monkeypatch, response: _FakeResponse):
    """Мок httpx.AsyncClient.post для multipart-uploads."""
    async def fake_post(self, url, **kwargs):
        return response
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


def test_openai_whisper_parses_segments(monkeypatch, fake_audio):
    body = {
        "text": "Привет это тест.",
        "language": "ru",
        "duration": 3.5,
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.5, "text": " Привет"},
            {"id": 1, "start": 1.5, "end": 3.5, "text": " это тест."},
        ],
    }
    _patch_openai_whisper(monkeypatch, _FakeResponse(200, body))

    client = OpenAIWhisperClient()
    res = asyncio.run(client.transcribe(
        audio_path=fake_audio, api_key="sk-test", language="ru",
    ))
    assert res.text == "Привет это тест."
    assert res.language == "ru"
    assert res.provider == "openai_whisper"
    assert res.duration_sec == 3.5
    assert len(res.segments) == 2
    # Текст strip-нут (от leading space)
    assert res.segments[0] == {"start": 0.0, "end": 1.5, "text": "Привет"}
    assert res.segments[1] == {"start": 1.5, "end": 3.5, "text": "это тест."}


def test_openai_whisper_no_segments_returns_empty(monkeypatch, fake_audio):
    """Если провайдер вернул text без segments — segments=[]."""
    body = {"text": "hello", "language": "en", "duration": 1.0}
    _patch_openai_whisper(monkeypatch, _FakeResponse(200, body))

    client = OpenAIWhisperClient()
    res = asyncio.run(client.transcribe(
        audio_path=fake_audio, api_key="sk-test", language=None,
    ))
    assert res.text == "hello"
    assert res.segments == []


def test_openai_whisper_error_raises(monkeypatch, fake_audio):
    _patch_openai_whisper(monkeypatch, _FakeResponse(401, {"error": "bad key"}))
    client = OpenAIWhisperClient()
    with pytest.raises(ProviderError, match="openai_whisper_failed"):
        asyncio.run(client.transcribe(
            audio_path=fake_audio, api_key="bad", language=None,
        ))


# ============================================================
# Groq Whisper — same verbose_json shape
# ============================================================

def test_groq_whisper_parses_segments(monkeypatch, fake_audio):
    body = {
        "text": "test",
        "language": "en",
        "duration": 2.0,
        "segments": [
            {"start": 0.0, "end": 2.0, "text": "test"},
        ],
    }
    async def fake_post(self, url, **kwargs):
        return _FakeResponse(200, body)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    client = GroqWhisperClient()
    res = asyncio.run(client.transcribe(
        audio_path=fake_audio, api_key="gsk_test", language="en",
    ))
    assert res.provider == "groq_whisper"
    assert res.model == "whisper-large-v3"
    assert len(res.segments) == 1
    assert res.segments[0]["text"] == "test"


def test_groq_whisper_handles_missing_start_end(monkeypatch, fake_audio):
    """Защита от частичных segments (нулевые/отсутствующие start/end)."""
    body = {
        "text": "x", "duration": 1.0,
        "segments": [
            {"text": "no times"},  # ни start, ни end
            {"start": 1.0, "text": "no end"},
        ],
    }
    async def fake_post(self, url, **kwargs):
        return _FakeResponse(200, body)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    client = GroqWhisperClient()
    res = asyncio.run(client.transcribe(
        audio_path=fake_audio, api_key="x", language=None,
    ))
    # Не падает: пустые start/end дефолтятся в 0
    assert res.segments[0] == {"start": 0.0, "end": 0.0, "text": "no times"}
    assert res.segments[1] == {"start": 1.0, "end": 0.0, "text": "no end"}


# ============================================================
# Deepgram — utterances[] вместо segments
# ============================================================

def test_deepgram_parses_utterances_as_segments(monkeypatch, fake_audio):
    body = {
        "metadata": {"duration": 4.2},
        "results": {
            "channels": [
                {
                    "detected_language": "ru",
                    "alternatives": [{"transcript": "Привет тест."}],
                },
            ],
            "utterances": [
                {"start": 0.0, "end": 1.8, "transcript": "Привет"},
                {"start": 1.8, "end": 4.2, "transcript": "тест."},
            ],
        },
    }
    async def fake_post(self, url, **kwargs):
        # Проверяем что utterances=true передан
        assert kwargs["params"]["utterances"] == "true"
        return _FakeResponse(200, body)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    client = DeepgramClient()
    res = asyncio.run(client.transcribe(
        audio_path=fake_audio, api_key="dgkey", language=None,
    ))
    assert res.text == "Привет тест."
    assert res.language == "ru"
    assert res.provider == "deepgram"
    assert len(res.segments) == 2
    assert res.segments[0] == {"start": 0.0, "end": 1.8, "text": "Привет"}


def test_deepgram_no_utterances_returns_empty_segments(monkeypatch, fake_audio):
    body = {
        "metadata": {"duration": 1.0},
        "results": {
            "channels": [{"alternatives": [{"transcript": "hi"}]}],
        },
    }
    async def fake_post(self, url, **kwargs):
        return _FakeResponse(200, body)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    client = DeepgramClient()
    res = asyncio.run(client.transcribe(
        audio_path=fake_audio, api_key="dg", language="en",
    ))
    assert res.text == "hi"
    assert res.segments == []


def test_deepgram_bad_response_shape_raises(monkeypatch, fake_audio):
    body = {"results": {"channels": []}}  # нет alternatives
    async def fake_post(self, url, **kwargs):
        return _FakeResponse(200, body)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    client = DeepgramClient()
    with pytest.raises(ProviderError, match="deepgram_bad_response"):
        asyncio.run(client.transcribe(
            audio_path=fake_audio, api_key="x", language="en",
        ))


# ============================================================
# AssemblyAI — другой API: upload + create + poll
# ============================================================

def test_assemblyai_parses_utterances_with_ms_timestamps(monkeypatch, fake_audio):
    """AssemblyAI возвращает start/end в МИЛЛИСЕКУНДАХ — клиент должен делить на 1000."""
    upload_resp = _FakeResponse(200, {"upload_url": "https://aa.io/u/123"})
    create_resp = _FakeResponse(200, {"id": "tr_42"})
    poll_resp = _FakeResponse(200, {
        "status": "completed",
        "text": "Привет тест.",
        "language_code": "ru",
        "audio_duration": 4.2,
        "utterances": [
            {"start": 0, "end": 1800, "text": "Привет"},        # ms
            {"start": 1800, "end": 4200, "text": " тест."},     # ms (с лидинг space)
        ],
    })

    async def fake_post(self, url, **kwargs):
        if url.endswith("/upload"):
            return upload_resp
        if url.endswith("/transcript"):
            return create_resp
        raise AssertionError(f"unexpected POST {url}")

    async def fake_get(self, url, **kwargs):
        return poll_resp

    async def no_sleep(*a, **kw):  # ускорение poll-loop в тесте
        pass

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    client = AssemblyAIClient()
    res = asyncio.run(client.transcribe(
        audio_path=fake_audio, api_key="aai", language="ru",
    ))
    assert res.text == "Привет тест."
    assert res.language == "ru"
    assert res.duration_sec == 4.2
    assert res.provider == "assemblyai"
    assert len(res.segments) == 2
    # Преобразование ms → seconds
    assert res.segments[0] == {"start": 0.0, "end": 1.8, "text": "Привет"}
    assert res.segments[1] == {"start": 1.8, "end": 4.2, "text": "тест."}


def test_assemblyai_error_status_raises(monkeypatch, fake_audio):
    upload_resp = _FakeResponse(200, {"upload_url": "https://aa.io/u/x"})
    create_resp = _FakeResponse(200, {"id": "tr_err"})
    poll_resp = _FakeResponse(200, {"status": "error", "error": "audio_too_short"})

    async def fake_post(self, url, **kwargs):
        if url.endswith("/upload"): return upload_resp
        return create_resp

    async def fake_get(self, url, **kwargs):
        return poll_resp

    async def no_sleep(*a, **kw):
        pass

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    client = AssemblyAIClient()
    with pytest.raises(ProviderError, match="audio_too_short"):
        asyncio.run(client.transcribe(
            audio_path=fake_audio, api_key="aai", language=None,
        ))
