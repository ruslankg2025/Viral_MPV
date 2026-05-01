"""Тесты InsightsStore + insights router (POST/GET/health)."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from insights.store import InsightsStore


# ─── InsightsStore tests ──────────────────────────────────────────────


def test_upsert_blog_daily_creates(tmp_path: Path):
    store = InsightsStore(tmp_path / "insights.db")
    rid, created = store.upsert_blog_daily(
        respondent="Алина",
        account_id=None,
        response_date="2026-05-01",
        responded_at="2026-05-01T08:30:00+00:00",
        data={"views": 80614, "reach": 54423},
    )
    assert created is True
    assert rid == 1


def test_upsert_blog_daily_updates_same_day(tmp_path: Path):
    store = InsightsStore(tmp_path / "insights.db")
    rid1, c1 = store.upsert_blog_daily(
        respondent="Алина", account_id=None, response_date="2026-05-01",
        responded_at=None, data={"views": 100},
    )
    rid2, c2 = store.upsert_blog_daily(
        respondent="Алина", account_id=None, response_date="2026-05-01",
        responded_at=None, data={"views": 200, "reach": 50},
    )
    assert c1 is True and c2 is False
    assert rid1 == rid2  # тот же row, не дубль
    rows = store.list_blog_daily(days=30)
    assert len(rows) == 1
    assert rows[0]["data"]["views"] == 200
    assert rows[0]["data"]["reach"] == 50


def test_upsert_blog_daily_multi_account(tmp_path: Path):
    """Один respondent + одна дата + два разных account_id → 2 записи,
    UNIQUE INDEX не блокирует. Регрессия на критику Plan-агента."""
    store = InsightsStore(tmp_path / "insights.db")
    _, c1 = store.upsert_blog_daily(
        respondent="Алина", account_id="acc1", response_date="2026-05-01",
        responded_at=None, data={"views": 100},
    )
    _, c2 = store.upsert_blog_daily(
        respondent="Алина", account_id="acc2", response_date="2026-05-01",
        responded_at=None, data={"views": 200},
    )
    assert c1 is True and c2 is True
    rows = store.list_blog_daily(days=30)
    assert len(rows) == 2
    by_acc = {r["account_id"]: r["data"]["views"] for r in rows}
    assert by_acc == {"acc1": 100, "acc2": 200}


def test_list_blog_daily_filters_by_days(tmp_path: Path):
    store = InsightsStore(tmp_path / "insights.db")
    today = datetime.now(timezone.utc).date()
    for offset in [40, 20, 5, 0]:
        d = (today - timedelta(days=offset)).isoformat()
        store.upsert_blog_daily(
            respondent=f"r{offset}", account_id=None, response_date=d,
            responded_at=None, data={"views": offset},
        )
    rows7 = store.list_blog_daily(days=7)
    assert len(rows7) == 2  # 5 и 0
    rows30 = store.list_blog_daily(days=30)
    assert len(rows30) == 3  # 20, 5, 0
    # ASC сортировка по дате
    dates = [r["response_date"] for r in rows30]
    assert dates == sorted(dates)


def test_get_health_no_data(tmp_path: Path):
    store = InsightsStore(tmp_path / "insights.db")
    h = store.get_health()
    assert h == {"latest_post": None, "stale_days": None, "total_rows": 0}


def test_get_health_with_data(tmp_path: Path):
    store = InsightsStore(tmp_path / "insights.db")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    store.upsert_blog_daily(
        respondent="Алина", account_id=None, response_date="2026-05-01",
        responded_at=yesterday, data={"views": 100},
    )
    h = store.get_health()
    assert h["total_rows"] == 1
    assert h["latest_post"] is not None
    # 2 дня прошло (с поправкой на округление через days)
    assert h["stale_days"] in (1, 2)


def test_check_constraint_rejects_bad_date(tmp_path: Path):
    """SQL CHECK constraint должен отклонять кривые форматы даты."""
    import sqlite3
    store = InsightsStore(tmp_path / "insights.db")
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_blog_daily(
            respondent="r", account_id=None, response_date="01.05.2026",
            responded_at=None, data={},
        )


# ─── Router tests (POST/GET/health через TestClient) ──────────────────


@pytest.fixture
def app_with_insights(tmp_path, monkeypatch):
    """Поднимает мини-FastAPI с insights-router и временной БД.
    Не запускаем полный shell-lifespan (не нужны runner/clients)."""
    from fastapi import FastAPI

    from insights.router import router as insights_router
    from orchestrator.state import state

    state.insights_store = InsightsStore(tmp_path / "insights.db")
    app = FastAPI()
    app.include_router(insights_router)

    yield app

    state.insights_store = None


def test_post_blog_daily_disabled_when_no_token_in_env(
    app_with_insights, monkeypatch
):
    monkeypatch.delenv("INSIGHTS_WRITE_TOKEN", raising=False)
    with TestClient(app_with_insights) as c:
        r = c.post("/api/insights/blog-daily", json={
            "respondent": "Алина",
            "response_date": "2026-05-01",
            "data": {"views": 100},
        })
    assert r.status_code == 503
    assert r.json()["detail"] == "insights_write_disabled"


def test_post_blog_daily_requires_token(app_with_insights, monkeypatch):
    monkeypatch.setenv("INSIGHTS_WRITE_TOKEN", "secret")
    with TestClient(app_with_insights) as c:
        r = c.post("/api/insights/blog-daily", json={
            "respondent": "Алина",
            "response_date": "2026-05-01",
            "data": {"views": 100},
        })
    # без header → 401
    assert r.status_code == 401


def test_post_blog_daily_wrong_token(app_with_insights, monkeypatch):
    monkeypatch.setenv("INSIGHTS_WRITE_TOKEN", "secret")
    with TestClient(app_with_insights) as c:
        r = c.post(
            "/api/insights/blog-daily",
            headers={"X-Worker-Token": "wrong"},
            json={
                "respondent": "Алина",
                "response_date": "2026-05-01",
                "data": {"views": 100},
            },
        )
    assert r.status_code == 401


def test_post_then_get_e2e(app_with_insights, monkeypatch):
    monkeypatch.setenv("INSIGHTS_WRITE_TOKEN", "secret")
    with TestClient(app_with_insights) as c:
        # POST с правильным токеном
        r = c.post(
            "/api/insights/blog-daily",
            headers={"X-Worker-Token": "secret"},
            json={
                "respondent": "Алина",
                "response_date": "2026-05-01",
                "data": {
                    "views": 80614, "reach": 54423,
                    "subs_growth": 20, "interactions": 3800,
                    "subs_pct": 4.9, "non_subs_pct": 95.1,
                    "reels_pct": 99.1, "posts_pct": 0.8, "stories_pct": 0.2,
                },
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["created"] is True
        assert body["id"] == 1

        # Повторный POST → updated
        r2 = c.post(
            "/api/insights/blog-daily",
            headers={"X-Worker-Token": "secret"},
            json={
                "respondent": "Алина",
                "response_date": "2026-05-01",
                "data": {"views": 90000},
            },
        )
        assert r2.json() == {"id": 1, "created": False}

        # GET без auth → 200
        rg = c.get("/api/insights/blog-daily?days=30")
        assert rg.status_code == 200
        rows = rg.json()
        assert len(rows) == 1
        assert rows[0]["views"] == 90000  # обновлённое значение
        assert rows[0]["date"] == "2026-05-01"

        # health endpoint
        rh = c.get("/api/insights/health")
        assert rh.status_code == 200
        assert rh.json()["total_rows"] == 1


def test_get_blog_daily_no_auth_required(app_with_insights):
    with TestClient(app_with_insights) as c:
        r = c.get("/api/insights/blog-daily?days=30")
    assert r.status_code == 200
    assert r.json() == []
