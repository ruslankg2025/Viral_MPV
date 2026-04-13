"""Unit-тесты text generation клиентов с httpx-моками."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from viral_llm.clients.anthropic_text import AnthropicTextClient
from viral_llm.clients.base import GenerationResult, ProviderError
from viral_llm.clients.openai_text import OpenAITextClient
from viral_llm.clients.registry import (
    TEXT_GENERATION_CLIENTS,
    get_text_client,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)[:500]

    def json(self):
        return self._payload


def _patch_httpx_post(monkeypatch, response: _FakeResponse):
    captured: dict = {}

    async def fake_post(self, url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        captured["json"] = kwargs.get("json", {})
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return captured


def test_registry_exposes_text_clients():
    assert "anthropic_claude_text" in TEXT_GENERATION_CLIENTS
    assert "openai_gpt4o_text" in TEXT_GENERATION_CLIENTS
    assert get_text_client("anthropic_claude_text").default_model == "claude-sonnet-4-6"
    assert get_text_client("openai_gpt4o_text").default_model == "gpt-4o"


def test_get_text_client_unknown_raises():
    with pytest.raises(ValueError):
        get_text_client("nonsense_llm")


def test_anthropic_text_happy_path(monkeypatch):
    client = AnthropicTextClient()
    response = _FakeResponse(
        200,
        {
            "content": [
                {"type": "text", "text": '{"hook": "H", "body": []}'},
            ],
            "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 123, "output_tokens": 45},
        },
    )
    captured = _patch_httpx_post(monkeypatch, response)

    result = asyncio.run(
        client.generate(system="sys", user="usr", api_key="sk-ant-fake")
    )

    assert isinstance(result, GenerationResult)
    assert result.text == '{"hook": "H", "body": []}'
    assert result.provider == "anthropic_claude_text"
    assert result.model == "claude-sonnet-4-6"
    assert result.input_tokens == 123
    assert result.output_tokens == 45
    assert result.latency_ms >= 0
    # Проверим, что в запросе отправили нужные поля
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "sk-ant-fake"
    assert captured["json"]["system"] == "sys"
    assert captured["json"]["messages"][0]["content"] == "usr"


def test_anthropic_text_error_raises_provider_error(monkeypatch):
    client = AnthropicTextClient()
    response = _FakeResponse(429, {"error": "rate_limited"})
    _patch_httpx_post(monkeypatch, response)

    with pytest.raises(ProviderError) as exc:
        asyncio.run(client.generate(system="s", user="u", api_key="k"))
    assert "anthropic_text_failed" in str(exc.value)
    assert "429" in str(exc.value)


def test_openai_text_happy_path(monkeypatch):
    client = OpenAITextClient()
    response = _FakeResponse(
        200,
        {
            "choices": [{"message": {"content": '{"ok": 1}'}}],
            "model": "gpt-4o-2024-11-20",
            "usage": {"prompt_tokens": 50, "completion_tokens": 20},
        },
    )
    captured = _patch_httpx_post(monkeypatch, response)

    result = asyncio.run(
        client.generate(system="sys", user="usr", api_key="sk-openai-fake")
    )

    assert result.text == '{"ok": 1}'
    assert result.provider == "openai_gpt4o_text"
    assert result.model == "gpt-4o-2024-11-20"
    assert result.input_tokens == 50
    assert result.output_tokens == 20
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-openai-fake"
    assert captured["json"]["response_format"] == {"type": "json_object"}
    msgs = captured["json"]["messages"]
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert msgs[1] == {"role": "user", "content": "usr"}


def test_openai_text_bad_response_raises(monkeypatch):
    client = OpenAITextClient()
    response = _FakeResponse(200, {"choices": []})
    _patch_httpx_post(monkeypatch, response)

    with pytest.raises(ProviderError) as exc:
        asyncio.run(client.generate(system="s", user="u", api_key="k"))
    assert "openai_text_bad_response" in str(exc.value)
