import pytest
from fastapi.testclient import TestClient

import main
from platforms.instagram import InstagramSource
from platforms.youtube import YouTubeSource
from state import state


USER_HEADERS = {"X-Token": "test-user-token"}
ADMIN_HEADERS = {"X-Admin-Token": "test-admin-token"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_DIR", str(tmp_path))
    monkeypatch.setenv("MONITOR_FAKE_FETCH", "true")
    from config import get_settings
    get_settings.cache_clear()

    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def ig_source():
    return InstagramSource(fake_mode=True)


# ------------------------------------------------------------------ #
# fetch_profile unit tests
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_fetch_profile_fake_public(ig_source):
    profile = await ig_source.fetch_profile("fake_public")
    assert profile is not None
    assert profile.username == "fake_public"
    assert profile.full_name == "Fake Public"
    assert profile.followers_count == 45000
    assert profile.posts_count == 120
    assert profile.avatar_url == "https://example.com/a.jpg"
    assert profile.is_verified is True
    assert profile.is_private is False
    assert profile.business_category == "Money"


@pytest.mark.asyncio
async def test_fetch_profile_fake_private(ig_source):
    profile = await ig_source.fetch_profile("fake_private")
    assert profile is not None
    assert profile.is_private is True
    assert profile.followers_count == 0


@pytest.mark.asyncio
async def test_fetch_profile_nonexistent_returns_none(ig_source):
    profile = await ig_source.fetch_profile("totally_unknown_handle_xyz")
    assert profile is None


# ------------------------------------------------------------------ #
# router integration tests
# ------------------------------------------------------------------ #

def test_private_blocks_source_creation(client):
    body = {
        "account_id": "acc-priv",
        "platform": "instagram",
        "channel_url": "https://instagram.com/fake_private",
    }
    r = client.post("/monitor/sources", json=body, headers=USER_HEADERS)
    assert r.status_code == 409, r.text
    assert "private_profile_not_supported" in r.json()["detail"]


def test_trigger_crawl_cooldown(client):
    # Create a public source first
    body = {
        "account_id": "acc-cool",
        "platform": "instagram",
        "channel_url": "https://instagram.com/fake_public",
    }
    r = client.post("/monitor/sources", json=body, headers=USER_HEADERS)
    assert r.status_code == 201, r.text
    src_id = r.json()["id"]
    # profile_fetched_at was set at creation

    # First crawl triggers profile refresh (but cooldown prevents it since just fetched)
    r1 = client.post(f"/monitor/sources/{src_id}/crawl", headers=USER_HEADERS)
    assert r1.status_code == 202, r1.text

    # Second crawl within 10-min window should also succeed (cooldown skips fetch_profile)
    r2 = client.post(f"/monitor/sources/{src_id}/crawl", headers=USER_HEADERS)
    assert r2.status_code == 202, r2.text


def test_private_detection_closes_watchlist(client):
    # 1. Create a public source
    body = {
        "account_id": "acc-priv-detect",
        "platform": "instagram",
        "channel_url": "https://instagram.com/fake_public",
    }
    r = client.post("/monitor/sources", json=body, headers=USER_HEADERS)
    assert r.status_code == 201, r.text
    src_id = r.json()["id"]

    # 2. Seed an active watchlist entry manually
    store = state.store
    from datetime import datetime, timedelta, timezone
    from storage import MonitorStore
    # Insert a fake video + watchlist
    video, _ = store.upsert_video(
        source_id=src_id,
        platform="instagram",
        external_id="test_reel_1",
        url="https://instagram.com/reel/test1",
        title="Test Reel",
    )
    store.insert_snapshot(video_id=video.id, views=100, likes=10, comments=5)
    wl = store.add_to_watchlist(
        video_id=video.id,
        source_id=src_id,
        published_at=datetime.now(timezone.utc).isoformat(),
        initial_views=100,
        initial_velocity=50.0,
        ttl_days=3,
    )
    assert wl is not None
    assert wl.status == "active"

    # 3. Clear profile_fetched_at so cooldown doesn't block
    with store._conn() as c:
        c.execute("UPDATE sources SET profile_fetched_at = NULL WHERE id = ?", (src_id,))

    # 4. Swap instagram platform to return private profile
    from platforms.base import ProfileInfo
    original_platform = state.platforms["instagram"]

    class FakePrivatePlatform:
        name = "instagram"

        async def fetch_profile(self, handle):
            return ProfileInfo(username=handle, is_private=True)

        async def resolve_channel(self, url):
            return await original_platform.resolve_channel(url)

        async def fetch_new_videos(self, *a, **kw):
            return await original_platform.fetch_new_videos(*a, **kw)

        async def fetch_metrics(self, *a, **kw):
            return await original_platform.fetch_metrics(*a, **kw)

    state.platforms["instagram"] = FakePrivatePlatform()
    try:
        r = client.post(f"/monitor/sources/{src_id}/crawl", headers=USER_HEADERS)
        assert r.status_code == 202, r.text
        data = r.json()
        assert data["status"] == "failed"
        assert data["error"] == "profile_went_private"
    finally:
        state.platforms["instagram"] = original_platform

    # 5. Check watchlist was closed
    wl_after = store.get_watchlist(wl.id)
    assert wl_after.status == "closed"

    # 6. Check source is deactivated
    src = store.get_source(src_id)
    assert src.is_active is False
    assert src.last_error == "profile_went_private"


# ------------------------------------------------------------------ #
# Protocol stub test
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_protocol_fetch_profile_stub_youtube():
    yt = YouTubeSource(fake_mode=True)
    result = await yt.fetch_profile("any_handle")
    assert result is None
