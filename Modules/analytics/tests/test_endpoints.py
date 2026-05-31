"""Smoke-тесты эндпоинтов через FastAPI TestClient (fetcher выключен)."""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_DIR", str(tmp_path))
    monkeypatch.setenv("ENABLE_FETCHER", "0")
    monkeypatch.setenv("ANALYTICS_USE_MOCK", "1")
    monkeypatch.setenv("ANALYTICS_TOKEN", "test-token")

    # get_settings закэширован — сбрасываем кэш под новые env.
    import config
    config.get_settings.cache_clear()
    import main
    importlib.reload(main)

    with TestClient(main.app) as c:
        yield c


def test_healthz(client):
    r = client.get("/analytics/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "metric_rows" in body


def test_refresh_now_requires_token(client):
    r = client.post("/analytics/refresh-now")
    assert r.status_code == 401


def test_refresh_now_then_aggregations(client):
    r = client.post("/analytics/refresh-now", headers={"X-Worker-Token": "test-token"})
    assert r.status_code == 200, r.text
    assert r.json()["refreshed"] is True
    # mock-режим записал метрики → агрегации непустые
    cp = client.get("/analytics/cross-platform?period=all").json()
    assert cp["publications"] >= 1
    assert cp["totals"]["views"] > 0

    top = client.get("/analytics/top?metric=views&period=all&limit=5").json()
    assert len(top["items"]) >= 1

    plat = client.get("/analytics/platform/vk?period=all").json()
    assert plat["platform"] == "vk"
    assert plat["publications_count"] >= 1


def test_publication_not_found(client):
    r = client.get("/analytics/publication/ghost-id")
    assert r.status_code == 404


def test_ab_requires_ids(client):
    r = client.get("/analytics/ab?ids=")
    assert r.status_code == 400
