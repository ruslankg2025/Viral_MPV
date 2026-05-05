"""Тесты services router: /api/services/status — Apify usage + наличие ключей."""
import os

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import router as services_module
from services.router import router as services_router


@pytest.fixture
def client(monkeypatch):
    # Чистим in-memory кэш между тестами — т.к. модуль импортируется один раз.
    services_module._CACHE.clear()
    app = FastAPI()
    app.include_router(services_router)
    return TestClient(app)


def _patch_apify_responses(monkeypatch, *, usage_status=200, usage_payload=None,
                          limits_status=200, limits_payload=None):
    """Мок httpx.AsyncClient.get для Apify-эндпоинтов."""
    async def fake_get(self, url, headers=None):
        if "usage/monthly" in url:
            req = httpx.Request("GET", url)
            return httpx.Response(usage_status, json=usage_payload or {}, request=req)
        if "limits" in url:
            req = httpx.Request("GET", url)
            return httpx.Response(limits_status, json=limits_payload or {}, request=req)
        raise AssertionError(f"unexpected url: {url}")
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)


def test_status_apify_ok(client, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    _patch_apify_responses(
        monkeypatch,
        usage_payload={"data": {"monthlyUsageUsd": 12.5}},
        limits_payload={"data": {"current": {"monthlyUsageHardLimitUsd": 50.0}}},
    )
    r = client.get("/api/services/status")
    assert r.status_code == 200
    data = r.json()
    apify = next(s for s in data["services"] if s["key"] == "apify")
    assert apify["configured"] is True
    assert apify["status"] == "ok"
    assert apify["used_usd"] == 12.5
    assert apify["limit_usd"] == 50.0
    assert apify["pct"] == 25.0
    assert apify["severity"] == "ok"


def test_status_apify_critical_above_90(client, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    _patch_apify_responses(
        monkeypatch,
        usage_payload={"data": {"monthlyUsageUsd": 47.0}},
        limits_payload={"data": {"current": {"monthlyUsageHardLimitUsd": 50.0}}},
    )
    r = client.get("/api/services/status")
    apify = next(s for s in r.json()["services"] if s["key"] == "apify")
    assert apify["pct"] == 94.0
    assert apify["severity"] == "critical"


def test_status_apify_blocked_at_100(client, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    _patch_apify_responses(
        monkeypatch,
        usage_payload={"data": {"monthlyUsageUsd": 50.0}},
        limits_payload={"data": {"monthlyUsageHardLimitUsd": 50.0}},  # без current — fallback
    )
    r = client.get("/api/services/status")
    apify = next(s for s in r.json()["services"] if s["key"] == "apify")
    assert apify["severity"] == "blocked"
    assert apify["pct"] == 100.0


def test_status_apify_token_missing(client, monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    r = client.get("/api/services/status")
    apify = next(s for s in r.json()["services"] if s["key"] == "apify")
    assert apify["configured"] is False
    assert apify["status"] == "missing"


def test_status_apify_http_error(client, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    _patch_apify_responses(
        monkeypatch,
        usage_status=403, usage_payload={"error": {"message": "forbidden"}},
        limits_status=200, limits_payload={"data": {}},
    )
    r = client.get("/api/services/status")
    apify = next(s for s in r.json()["services"] if s["key"] == "apify")
    assert apify["status"] == "error"
    assert "403" in apify["error"]


def test_status_other_services_listed(client, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    monkeypatch.setenv("BOOTSTRAP_OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ANTHROPIC_API_KEY", raising=False)
    _patch_apify_responses(
        monkeypatch,
        usage_payload={"data": {}}, limits_payload={"data": {}},
    )
    r = client.get("/api/services/status")
    services = r.json()["services"]
    keys = [s["key"] for s in services]
    # Apify + 6 other services
    assert keys == ["apify", "openai", "anthropic", "assemblyai",
                    "deepgram", "groq", "gemini"]
    openai = next(s for s in services if s["key"] == "openai")
    assert openai["configured"] is True  # BOOTSTRAP_OPENAI_API_KEY задан
    anthropic = next(s for s in services if s["key"] == "anthropic")
    assert anthropic["configured"] is False
    assert anthropic["status"] == "missing"


def test_cache_returns_same_payload(client, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    call_count = {"n": 0}

    async def fake_get(self, url, headers=None):
        call_count["n"] += 1
        req = httpx.Request("GET", url)
        if "usage/monthly" in url:
            return httpx.Response(200, json={"data": {"monthlyUsageUsd": 1.0}}, request=req)
        return httpx.Response(200, json={"data": {"monthlyUsageHardLimitUsd": 10.0}}, request=req)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    r1 = client.get("/api/services/status")
    r2 = client.get("/api/services/status")
    assert r1.json()["fetched_at"] == r2.json()["fetched_at"]
    # Должно было быть всего 2 fetch (usage + limits) — второй раз кэш.
    assert call_count["n"] == 2


def test_force_bypasses_cache(client, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    call_count = {"n": 0}

    async def fake_get(self, url, headers=None):
        call_count["n"] += 1
        req = httpx.Request("GET", url)
        if "usage/monthly" in url:
            return httpx.Response(200, json={"data": {"monthlyUsageUsd": 1.0}}, request=req)
        return httpx.Response(200, json={"data": {"monthlyUsageHardLimitUsd": 10.0}}, request=req)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    client.get("/api/services/status")
    client.get("/api/services/status?force=true")
    assert call_count["n"] == 4  # 2 + 2
