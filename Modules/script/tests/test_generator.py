"""Тесты generator с FakeTextClient — без сети."""
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from generator import GenContext, generate_with_retry  # noqa: E402
from schemas import GenerateParams  # noqa: E402
from viral_llm.clients.base import GenerationResult  # noqa: E402
from viral_llm.keys.crypto import KeyCrypto  # noqa: E402
from viral_llm.keys.resolver import KeyResolver  # noqa: E402
from viral_llm.keys.store import KeyStore  # noqa: E402
from viral_llm.clients import registry as reg  # noqa: E402


VALID_BODY = {
    "meta": {
        "template": "reels_hook_v1",
        "template_version": "v1",
        "language": "ru",
        "target_duration_sec": 30,
        "format": "reels",
    },
    "hook": {"text": "Интригующий hook", "estimated_duration_sec": 3.0},
    "body": [
        {"scene": 1, "text": "Сцена 1", "estimated_duration_sec": 10.0, "visual_hint": ""},
        {"scene": 2, "text": "Сцена 2", "estimated_duration_sec": 12.0, "visual_hint": ""},
    ],
    "cta": {"text": "Подпишись", "estimated_duration_sec": 4.0},
    "hashtags": ["#tag1", "#tag2", "#tag3"],
    "_schema_version": "1.0",
}


class _FakeClient:
    provider = "anthropic_claude_text"
    default_model = "claude-sonnet-4-6"

    def __init__(self, responses: list[str]):
        assert responses, "need at least one canned response"
        self._responses = list(responses)
        self._last = responses[-1]
        self.calls: list[dict[str, Any]] = []

    async def generate(self, *, system, user, api_key, max_tokens=2048, model=None):
        self.calls.append({"system": system, "user": user, "api_key": api_key})
        if self._responses:
            self._last = self._responses.pop(0)
        return GenerationResult(
            text=self._last,
            provider=self.provider,
            model=self.default_model,
            input_tokens=100,
            output_tokens=50,
            latency_ms=20,
        )


@pytest.fixture()
def store(tmp_path):
    crypto = KeyCrypto(Fernet.generate_key().decode())
    s = KeyStore(tmp_path / "keys.db", crypto)
    s.create(provider="anthropic_claude_text", label="t1", secret="sk-fake", priority=1)
    return s


def _ctx(template_body: str = "You are a scenario writer. language: {language}") -> GenContext:
    return GenContext(
        template_name="reels_hook_v1",
        template_version="v1",
        template_body=template_body,
        params=GenerateParams(topic="тема", duration_sec=30),
        profile={"niche": "tech"},
        provider=None,
    )


def test_happy_path_no_retry(store, monkeypatch):
    fake = _FakeClient([json.dumps(VALID_BODY, ensure_ascii=False)])
    monkeypatch.setitem(reg.TEXT_GENERATION_CLIENTS, "anthropic_claude_text", fake)

    first, retry = asyncio.run(
        generate_with_retry(
            ctx=_ctx(),
            resolver=KeyResolver(store),
            job_id="j1",
        )
    )
    assert first.status == "ok"
    assert retry is None
    assert first.body is not None
    assert first.body.hook.text == "Интригующий hook"
    assert len(fake.calls) == 1


def test_retry_on_validation_failed(store, monkeypatch):
    # Первая попытка — невалидная длительность (слишком короткая)
    bad = json.loads(json.dumps(VALID_BODY))
    bad["body"] = [{"scene": 1, "text": "s", "estimated_duration_sec": 2.0, "visual_hint": ""}]
    bad["hook"]["estimated_duration_sec"] = 1.0
    bad["cta"]["estimated_duration_sec"] = 1.0
    # total = 4 vs target 30 — fail

    fake = _FakeClient([
        json.dumps(bad, ensure_ascii=False),
        json.dumps(VALID_BODY, ensure_ascii=False),
    ])
    monkeypatch.setitem(reg.TEXT_GENERATION_CLIENTS, "anthropic_claude_text", fake)

    first, retry = asyncio.run(
        generate_with_retry(
            ctx=_ctx(),
            resolver=KeyResolver(store),
            job_id="j2",
        )
    )
    assert first.status == "validation_failed"
    assert retry is not None
    assert retry.status == "ok"
    assert len(fake.calls) == 2
    # retry получил system_addendum с причиной
    assert "Previous attempt failed" in fake.calls[1]["system"]


def test_retry_also_fails_returns_failed(store, monkeypatch):
    bad = json.loads(json.dumps(VALID_BODY))
    bad["body"] = [{"scene": 1, "text": "s", "estimated_duration_sec": 2.0, "visual_hint": ""}]
    bad["hook"]["estimated_duration_sec"] = 1.0
    bad["cta"]["estimated_duration_sec"] = 1.0

    fake = _FakeClient([
        json.dumps(bad, ensure_ascii=False),
        json.dumps(bad, ensure_ascii=False),
    ])
    monkeypatch.setitem(reg.TEXT_GENERATION_CLIENTS, "anthropic_claude_text", fake)

    first, retry = asyncio.run(
        generate_with_retry(
            ctx=_ctx(),
            resolver=KeyResolver(store),
            job_id="j3",
        )
    )
    assert first.status == "validation_failed"
    assert retry is not None
    assert retry.status == "validation_failed"
    assert len(fake.calls) == 2


def test_unparseable_json_is_error(store, monkeypatch):
    fake = _FakeClient(["this is not json at all"])
    monkeypatch.setitem(reg.TEXT_GENERATION_CLIENTS, "anthropic_claude_text", fake)

    first, retry = asyncio.run(
        generate_with_retry(
            ctx=_ctx(),
            resolver=KeyResolver(store),
            job_id="j4",
        )
    )
    assert first.status == "error"
    violations = first.constraints_report["violations"]
    assert any(v["code"] == "json_parse_failed" for v in violations)
    assert retry is not None  # один retry всегда пытается
