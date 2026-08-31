"""Phase 4 — anti-spam / schedule randomization (чистая логика, без сети)."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from antispam import (
    antispam_warnings,
    content_hash,
    spread_schedule,
)
from storage import PublicationStore

HDR = {"X-Worker-Token": "test-worker-token"}

BASE = "2030-01-01T12:00:00+00:00"


def _minutes_offset(result_iso: str, base_iso: str = BASE) -> float:
    a = datetime.fromisoformat(result_iso)
    b = datetime.fromisoformat(base_iso)
    return (a - b).total_seconds() / 60.0


# ── spread_schedule ───────────────────────────────────────────────────────────
def test_spread_reproducible():
    """Один вход → один и тот же результат (детерминизм, не random)."""
    a = spread_schedule(BASE, 2, step_min=15, jitter_min=15, seed="vk_video:2")
    b = spread_schedule(BASE, 2, step_min=15, jitter_min=15, seed="vk_video:2")
    assert a == b


def test_spread_within_bounds():
    """offset ∈ [index*step - jitter, index*step + jitter]."""
    step, jitter = 15, 15
    for index in range(0, 5):
        for seed in (f"p{index}", f"vk:{index}", str(index)):
            res = spread_schedule(BASE, index, step_min=step, jitter_min=jitter, seed=seed)
            off = _minutes_offset(res)
            assert index * step - jitter <= off <= index * step + jitter


def test_spread_separates_platforms():
    """Разные индексы дают разные времена (не одновременно)."""
    times = [
        spread_schedule(BASE, i, step_min=15, jitter_min=5, seed=f"plat:{i}")
        for i in range(3)
    ]
    assert len(set(times)) == 3


def test_spread_zero_jitter_is_exact_step():
    """jitter=0 → ровно index*step, без разброса."""
    res = spread_schedule(BASE, 3, step_min=10, jitter_min=0, seed="x")
    assert _minutes_offset(res) == 30


def test_spread_index_zero_no_step():
    """index=0 → сдвиг только в пределах jitter (шаг 0*step=0)."""
    res = spread_schedule(BASE, 0, step_min=15, jitter_min=15, seed="s")
    assert -15 <= _minutes_offset(res) <= 15


# ── content_hash ──────────────────────────────────────────────────────────────
def test_content_hash_stable_and_normalized():
    h1 = content_hash("Hello World", "Body")
    h2 = content_hash("  hello   world ", "body")
    assert h1 == h2  # регистр/пробелы не влияют


def test_content_hash_differs():
    assert content_hash("A", "x") != content_hash("B", "x")


# ── antispam_warnings: rate-cap ───────────────────────────────────────────────
def _store(tmp_path) -> PublicationStore:
    return PublicationStore(tmp_path / "db" / "pub.db")


def test_rate_cap_warns_after_limit(tmp_path):
    store = _store(tmp_path)
    # records стампятся реальным _now() при create → окно считаем от реального now,
    # чтобы тест был детерминирован относительно момента вставки (а не хрупкой даты).
    now = datetime.now(timezone.utc)
    for i in range(3):
        store.create(platform="vk_video", title=f"t{i}", status="published")
    warns = antispam_warnings(
        store, platform="vk_video", title="new", now=now,
        rate_window_hours=4, rate_limit=3, content_cooldown_hours=0,
    )
    assert any("rate_cap" in w for w in warns)


def test_rate_cap_no_warn_under_limit(tmp_path):
    store = _store(tmp_path)
    # records стампятся реальным _now() при create → окно считаем от реального now,
    # чтобы тест был детерминирован относительно момента вставки (а не хрупкой даты).
    now = datetime.now(timezone.utc)
    for i in range(2):
        store.create(platform="vk_video", title=f"t{i}", status="published")
    warns = antispam_warnings(
        store, platform="vk_video", title="new", now=now,
        rate_window_hours=4, rate_limit=3, content_cooldown_hours=0,
    )
    assert not any("rate_cap" in w for w in warns)


def test_rate_cap_per_platform(tmp_path):
    store = _store(tmp_path)
    # records стампятся реальным _now() при create → окно считаем от реального now,
    # чтобы тест был детерминирован относительно момента вставки (а не хрупкой даты).
    now = datetime.now(timezone.utc)
    for i in range(3):
        store.create(platform="vk_video", title=f"t{i}", status="published")
    # Другая платформа — лимит не превышен.
    warns = antispam_warnings(
        store, platform="telegram", title="new", now=now,
        rate_window_hours=4, rate_limit=3, content_cooldown_hours=0,
    )
    assert not any("rate_cap" in w for w in warns)


# ── antispam_warnings: content-cooldown ───────────────────────────────────────
def test_content_cooldown_warns_on_similar(tmp_path):
    store = _store(tmp_path)
    # records стампятся реальным _now() при create → окно считаем от реального now,
    # чтобы тест был детерминирован относительно момента вставки (а не хрупкой даты).
    now = datetime.now(timezone.utc)
    store.create(
        platform="vk_video", title="Viral Cats", description="so cute",
        status="published", content_hash=content_hash("Viral Cats", "so cute"),
    )
    warns = antispam_warnings(
        store, platform="vk_video",
        title="  viral   cats ", description="SO CUTE",  # отличается только формой
        now=now, rate_window_hours=0, content_cooldown_hours=24,
    )
    assert any("content_cooldown" in w for w in warns)


def test_content_cooldown_no_warn_on_different(tmp_path):
    store = _store(tmp_path)
    # records стампятся реальным _now() при create → окно считаем от реального now,
    # чтобы тест был детерминирован относительно момента вставки (а не хрупкой даты).
    now = datetime.now(timezone.utc)
    store.create(
        platform="vk_video", title="Viral Cats", description="cute",
        status="published", content_hash=content_hash("Viral Cats", "cute"),
    )
    warns = antispam_warnings(
        store, platform="vk_video", title="Totally Different", description="dogs",
        now=now, rate_window_hours=0, content_cooldown_hours=24,
    )
    assert not any("content_cooldown" in w for w in warns)


# ── storage query-методы ──────────────────────────────────────────────────────
def test_count_recent_ignores_failed(tmp_path):
    store = _store(tmp_path)
    # records стампятся реальным _now() при create → окно считаем от реального now,
    # чтобы тест был детерминирован относительно момента вставки (а не хрупкой даты).
    now = datetime.now(timezone.utc)
    store.create(platform="vk_video", title="ok", status="published")
    store.create(platform="vk_video", title="bad", status="failed")
    since = (now - timedelta(hours=4)).isoformat()
    assert store.count_recent_by_platform(platform="vk_video", since_iso=since) == 1

    # окно: since в будущем → ничего не попадает
    future = (now + timedelta(hours=1)).isoformat()
    assert store.count_recent_by_platform(platform="vk_video", since_iso=future) == 0


# ── интеграция через API (контракт PublishResp.warnings, разброс расписания) ──
@pytest.fixture
def client(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"UNEXPECTED HTTP: {request.url}")

    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        kwargs.pop("timeout", None)
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
    import main

    with TestClient(main.app) as c:
        yield c


def _publish(client, title, platforms, dry=True):
    return client.post("/publisher/publish", headers=HDR, json={
        "video_path": "/m/v.mp4", "title": title, "description": "d",
        "platforms": platforms, "dry_run": dry,
    })


def test_publish_resp_has_warnings_field(client):
    """Контракт не сломан: warnings присутствует и по умолчанию пуст."""
    body = _publish(client, "Fresh", ["vk_video"]).json()
    assert body["warnings"] == []


def test_publish_rate_cap_warning_via_api(client):
    # default antispam_rate_limit=3, window=4ч → 3 публикации → на 4-й warning.
    for i in range(3):
        _publish(client, f"t{i}", ["vk_video"])
    body = _publish(client, "t4", ["vk_video"]).json()
    assert any("rate_cap" in w for w in body["warnings"])


def test_publish_content_cooldown_warning_via_api(client):
    _publish(client, "Unique Title", ["vk_video"])
    # тот же контент (с иной формой) → content_cooldown
    body = _publish(client, "  unique   title ", ["vk_video"]).json()
    assert any("content_cooldown" in w for w in body["warnings"])


def test_schedule_spreads_multiple_platforms(client):
    r = client.post("/publisher/schedule", headers=HDR, json={
        "video_path": "/m/v.mp4", "title": "S", "description": "d",
        "platforms": ["vk_video", "vk_clips", "telegram"],
        "scheduled_at": "2999-01-01T00:00:00+00:00",
    })
    assert r.status_code == 200
    pubs = client.get("/publisher/publications?status=scheduled", headers=HDR).json()
    times = sorted(p["scheduled_at"] for p in pubs)
    # 3 площадки → 3 разных времени (не одновременно).
    assert len(set(times)) == 3


def test_schedule_single_platform_keeps_base_time(client):
    r = client.post("/publisher/schedule", headers=HDR, json={
        "video_path": "/m/v.mp4", "title": "One",
        "platforms": ["vk_video"], "scheduled_at": "2999-01-01T00:00:00+00:00",
    })
    assert r.status_code == 200
    pubs = client.get("/publisher/publications?status=scheduled", headers=HDR).json()
    assert pubs[0]["scheduled_at"] == "2999-01-01T00:00:00+00:00"
