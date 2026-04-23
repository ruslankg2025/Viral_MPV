import pytest
from fastapi.testclient import TestClient

import main
from state import state


USER_HEADERS = {"X-Token": "test-user-token"}
ADMIN_HEADERS = {"X-Admin-Token": "test-admin-token"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Переопределяем db_dir per test, чтобы изоляция
    monkeypatch.setenv("DB_DIR", str(tmp_path))
    monkeypatch.setenv("MONITOR_FAKE_FETCH", "true")
    from config import get_settings
    get_settings.cache_clear()

    with TestClient(main.app) as c:
        yield c


# ---------------- Healthz (public) ----------------

def test_healthz_no_auth_required(client):
    r = client.get("/monitor/healthz")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["fake_mode"] is True
    assert "active_sources" in data
    assert "youtube_quota_used_percent" in data
    assert "scheduler_running" in data


# ---------------- Auth matrix ----------------

def test_sources_requires_user_token(client):
    r = client.get("/monitor/sources")
    assert r.status_code == 401

    r = client.get("/monitor/sources", headers={"X-Token": "wrong"})
    assert r.status_code == 401

    r = client.get("/monitor/sources", headers=USER_HEADERS)
    assert r.status_code == 200
    assert r.json() == []


def test_admin_requires_admin_token(client):
    r = client.get("/monitor/admin/platforms")
    assert r.status_code == 401

    r = client.get("/monitor/admin/platforms", headers=USER_HEADERS)
    assert r.status_code == 401

    r = client.get("/monitor/admin/platforms", headers=ADMIN_HEADERS)
    assert r.status_code == 200


# ---------------- Sources CRUD ----------------

def test_create_source_via_fake_mode(client):
    body = {
        "account_id": "acc1",
        "platform": "youtube",
        "channel_url": "https://youtube.com/@mrbeast",
        "niche_slug": "entertainment",
        "tags": ["viral"],
        "priority": 100,
        "interval_min": 30,  # ниже plan floor → clamp к 360
    }
    r = client.post("/monitor/sources", json=body, headers=USER_HEADERS)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["account_id"] == "acc1"
    assert data["external_id"].startswith("UC")  # из fixture
    assert data["channel_name"] == "Fake Channel"
    assert data["is_active"] is True
    assert data["profile_validated"] is False  # profile недоступен
    # interval_min clamped к plan.min_interval_min=360
    assert data["interval_min"] == 360


def test_create_duplicate_source_returns_409(client):
    body = {
        "account_id": "acc1",
        "platform": "youtube",
        "channel_url": "https://youtube.com/@mrbeast",
    }
    r1 = client.post("/monitor/sources", json=body, headers=USER_HEADERS)
    assert r1.status_code == 201
    r2 = client.post("/monitor/sources", json=body, headers=USER_HEADERS)
    assert r2.status_code == 409


def test_create_source_bad_url(client):
    body = {
        "account_id": "acc1",
        "channel_url": "https://youtu.be/dQw4w9WgXcQ",
    }
    r = client.post("/monitor/sources", json=body, headers=USER_HEADERS)
    assert r.status_code == 400
    assert "resolve_failed" in r.json()["detail"]


def test_list_sources_filter_by_account(client):
    for acc in ["a1", "a1", "a2"]:
        client.post(
            "/monitor/sources",
            json={
                "account_id": acc,
                "channel_url": f"https://youtube.com/@test_{acc}_{hash(acc) & 0xffff}",
            },
            headers=USER_HEADERS,
        )
    # В fake mode все resolve возвращают тот же UC..., так что дубли будут с 409
    # Проверим только что список возвращается
    r = client.get("/monitor/sources?account_id=a1", headers=USER_HEADERS)
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_patch_source(client):
    body = {"account_id": "acc1", "channel_url": "https://youtube.com/@t"}
    r = client.post("/monitor/sources", json=body, headers=USER_HEADERS)
    source_id = r.json()["id"]

    # interval_min=15 ниже plan.min_interval_min=360, должно быть clamp'нуто.
    r = client.patch(
        f"/monitor/sources/{source_id}",
        json={"priority": 500, "interval_min": 15},
        headers=USER_HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["priority"] == 500
    assert r.json()["interval_min"] == 360  # clamped к plan floor


def test_delete_source(client):
    body = {"account_id": "acc1", "channel_url": "https://youtube.com/@t"}
    source_id = client.post("/monitor/sources", json=body, headers=USER_HEADERS).json()["id"]

    r = client.delete(f"/monitor/sources/{source_id}", headers=USER_HEADERS)
    assert r.status_code == 204

    r = client.get(f"/monitor/sources/{source_id}", headers=USER_HEADERS)
    assert r.status_code == 404


def test_trigger_crawl_creates_videos(client):
    body = {"account_id": "acc1", "channel_url": "https://youtube.com/@t"}
    source_id = client.post("/monitor/sources", json=body, headers=USER_HEADERS).json()["id"]

    r = client.post(f"/monitor/sources/{source_id}/crawl", headers=USER_HEADERS)
    assert r.status_code == 202
    data = r.json()
    assert data["status"] == "ok"
    assert data["videos_new"] == 3  # из fixture playlist

    # Видео должны появиться
    r = client.get(f"/monitor/videos?source_id={source_id}", headers=USER_HEADERS)
    assert r.status_code == 200
    assert len(r.json()) == 3


def test_video_detail_with_snapshots(client):
    body = {"account_id": "acc1", "channel_url": "https://youtube.com/@t"}
    source_id = client.post("/monitor/sources", json=body, headers=USER_HEADERS).json()["id"]
    client.post(f"/monitor/sources/{source_id}/crawl", headers=USER_HEADERS)

    videos = client.get(f"/monitor/videos?source_id={source_id}", headers=USER_HEADERS).json()
    video_id = videos[0]["id"]

    r = client.get(f"/monitor/videos/{video_id}", headers=USER_HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert "snapshots" in data
    assert len(data["snapshots"]) >= 1
    assert data["current_views"] > 0


def test_analyze_stub_returns_payload(client):
    body = {"account_id": "acc1", "channel_url": "https://youtube.com/@t"}
    source_id = client.post("/monitor/sources", json=body, headers=USER_HEADERS).json()["id"]
    client.post(f"/monitor/sources/{source_id}/crawl", headers=USER_HEADERS)

    videos = client.get(f"/monitor/videos?source_id={source_id}", headers=USER_HEADERS).json()
    video_id = videos[0]["id"]

    r = client.post(f"/monitor/videos/{video_id}/analyze", headers=USER_HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert data["file_path"] is None
    assert data["source_url"].startswith("https://")
    assert data["hints"]["platform"] == "youtube"


# ---------------- Admin ----------------

def test_admin_platforms(client):
    r = client.get("/monitor/admin/platforms", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    data = r.json()
    names = {p["name"] for p in data}
    assert names == {"youtube", "instagram", "tiktok"}
    # MONITOR_FAKE_FETCH=true → все платформы в fake
    assert all(p["fake_mode"] is True for p in data)


def test_admin_apify_usage_empty(client):
    r = client.get("/monitor/admin/platforms/apify/usage", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert "date" in data
    assert data["entries"] == []


def test_admin_youtube_quota(client):
    r = client.get("/monitor/admin/platforms/youtube/quota", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert data["limit"] == 10000
    assert "percent" in data
    assert "date" in data


def test_admin_scheduler_state(client):
    r = client.get("/monitor/admin/scheduler", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert "running" in data
    assert "jobs" in data


def test_admin_scheduler_reload(client):
    # Создадим source — получим job
    client.post(
        "/monitor/sources",
        json={"account_id": "a", "channel_url": "https://youtube.com/@x"},
        headers=USER_HEADERS,
    )
    r = client.post("/monitor/admin/scheduler/reload", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert r.json()["reloaded"] >= 1


# ---------------- 404s ----------------

def test_get_unknown_source_404(client):
    r = client.get("/monitor/sources/nonexistent-uuid", headers=USER_HEADERS)
    assert r.status_code == 404


def test_get_unknown_video_404(client):
    r = client.get("/monitor/videos/nonexistent", headers=USER_HEADERS)
    assert r.status_code == 404


# ---------------- Plan endpoints ----------------

def test_admin_get_plan_returns_seeded_defaults(client):
    r = client.get("/monitor/admin/plan", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert data["plan_name"] == "self"
    assert data["max_sources_total"] == 50
    assert data["min_interval_min"] == 360
    assert data["max_results_limit"] == 5
    assert data["crawl_anchor_utc"] == "00:00"
    assert data["sources_used"] == 0


def test_admin_get_plan_requires_admin(client):
    r = client.get("/monitor/admin/plan")
    assert r.status_code == 401
    r = client.get("/monitor/admin/plan", headers=USER_HEADERS)
    assert r.status_code == 401


def test_admin_put_plan_updates_fields(client):
    body = {
        "plan_name": "starter",
        "max_sources_total": 200,
        "min_interval_min": 60,
        "max_results_limit": 30,
        "crawl_anchor_utc": "03:00",
    }
    r = client.put("/monitor/admin/plan", json=body, headers=ADMIN_HEADERS)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["plan_name"] == "starter"
    assert data["max_sources_total"] == 200
    assert data["min_interval_min"] == 60
    assert data["max_results_limit"] == 30
    assert data["crawl_anchor_utc"] == "03:00"


def test_admin_put_plan_partial(client):
    r = client.put(
        "/monitor/admin/plan",
        json={"max_sources_total": 10},
        headers=ADMIN_HEADERS,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["max_sources_total"] == 10
    # Остальные остались на дефолтах
    assert data["min_interval_min"] == 360


def test_admin_put_plan_validates_anchor_format(client):
    r = client.put(
        "/monitor/admin/plan",
        json={"crawl_anchor_utc": "not-a-time"},
        headers=ADMIN_HEADERS,
    )
    assert r.status_code == 422


def test_create_source_clamps_interval_to_plan_floor(client):
    body = {
        "account_id": "a",
        "channel_url": "https://youtube.com/@t",
        "interval_min": 60,  # ниже 360
    }
    r = client.post("/monitor/sources", json=body, headers=USER_HEADERS)
    assert r.status_code == 201
    assert r.json()["interval_min"] == 360


def test_create_source_rejected_when_plan_cap_reached(client):
    # Понизим cap до 2 через admin
    client.put(
        "/monitor/admin/plan",
        json={"max_sources_total": 2},
        headers=ADMIN_HEADERS,
    )
    # Создаём 2 — ок
    for i in range(2):
        r = client.post(
            "/monitor/sources",
            json={
                "account_id": f"acc{i}",
                "channel_url": f"https://youtube.com/@user{i}",
            },
            headers=USER_HEADERS,
        )
        assert r.status_code == 201, r.text
    # Третий — rejected
    r = client.post(
        "/monitor/sources",
        json={
            "account_id": "acc3",
            "channel_url": "https://youtube.com/@user3",
        },
        headers=USER_HEADERS,
    )
    assert r.status_code == 409
    assert "plan_limit_reached" in r.json()["detail"]


def test_plan_change_reclamps_existing_sources(client):
    # Создаём источник с min=360
    body = {"account_id": "a", "channel_url": "https://youtube.com/@t"}
    r = client.post("/monitor/sources", json=body, headers=USER_HEADERS)
    source_id = r.json()["id"]
    assert r.json()["interval_min"] == 360

    # Поднимаем plan.min_interval_min до 720
    r = client.put(
        "/monitor/admin/plan",
        json={"min_interval_min": 720},
        headers=ADMIN_HEADERS,
    )
    assert r.status_code == 200

    # Существующий источник должен быть подтянут до 720
    r = client.get(f"/monitor/sources/{source_id}", headers=USER_HEADERS)
    assert r.json()["interval_min"] == 720
