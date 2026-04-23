from datetime import datetime, timedelta, timezone

import pytest

from crawler import orchestrate_crawl
from platforms.base import (
    ChannelInfo,
    ChannelNotFound,
    MetricsSnapshot,
    PlatformError,
    QuotaExhausted,
    VideoMeta,
)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class FakePlatform:
    name = "youtube"

    def __init__(
        self,
        new_videos: list[VideoMeta] | None = None,
        metrics: list[MetricsSnapshot] | None = None,
        raise_on_fetch_new: Exception | None = None,
        raise_on_fetch_metrics: Exception | None = None,
    ):
        self._new_videos = new_videos or []
        self._metrics = metrics or []
        self._raise_new = raise_on_fetch_new
        self._raise_metrics = raise_on_fetch_metrics

    async def resolve_channel(self, url: str) -> ChannelInfo:
        return ChannelInfo(external_id="UC123", channel_name="Fake")

    async def fetch_new_videos(self, channel, known, *, results_limit=None):
        if self._raise_new:
            raise self._raise_new
        self.last_results_limit = results_limit  # для ассертов
        return [v for v in self._new_videos if v.external_id not in known]

    async def fetch_metrics(self, ids):
        if self._raise_metrics:
            raise self._raise_metrics
        return [m for m in self._metrics if m.external_id in ids]


@pytest.fixture
def source_fixture(store):
    return store.create_source(
        account_id="acc1",
        platform="youtube",
        channel_url="https://youtube.com/@fake",
        external_id="UC123",
        channel_name="Fake",
    )


@pytest.mark.asyncio
async def test_happy_path_adds_videos_and_snapshots(store, source_fixture):
    now = datetime.now(timezone.utc)
    new_videos = [
        VideoMeta(
            external_id="v1",
            url="https://y/v1",
            title="V1",
            published_at=_iso(now - timedelta(days=1)),
        ),
        VideoMeta(
            external_id="v2",
            url="https://y/v2",
            title="V2",
            published_at=_iso(now - timedelta(days=3)),
        ),
    ]
    metrics = [
        MetricsSnapshot(external_id="v1", views=5000, likes=300, comments=20),
        MetricsSnapshot(external_id="v2", views=1000, likes=50, comments=5),
    ]
    platform = FakePlatform(new_videos=new_videos, metrics=metrics)

    result = await orchestrate_crawl(source_fixture, platform, store)

    assert result.status == "ok"
    assert result.videos_new == 2
    assert result.videos_updated == 2

    videos = store.list_videos(source_fixture.id)
    assert len(videos) == 2

    # Crawl log
    logs = store.list_crawl_log(source_id=source_fixture.id)
    assert len(logs) == 1
    assert logs[0].status == "ok"
    assert logs[0].videos_new == 2

    # Source updated
    updated = store.get_source(source_fixture.id)
    assert updated.last_crawled_at is not None


@pytest.mark.asyncio
async def test_existing_videos_only_updates_metrics(store, source_fixture):
    # Первый обход
    now = datetime.now(timezone.utc)
    new_videos = [
        VideoMeta(external_id="v1", url="u1", title="V1", published_at=_iso(now - timedelta(days=1))),
    ]
    metrics1 = [MetricsSnapshot(external_id="v1", views=100, likes=10, comments=1)]
    await orchestrate_crawl(source_fixture, FakePlatform(new_videos, metrics1), store)

    # Второй обход — тех же видео не возвращаем (они в known)
    metrics2 = [MetricsSnapshot(external_id="v1", views=500, likes=50, comments=5)]
    result = await orchestrate_crawl(source_fixture, FakePlatform([], metrics2), store)

    assert result.videos_new == 0
    assert result.videos_updated == 1

    v = store.list_videos(source_fixture.id)[0]
    snaps = store.list_snapshots(v.id)
    assert len(snaps) == 2
    assert snaps[0].views == 500  # latest first
    assert snaps[1].views == 100


@pytest.mark.asyncio
async def test_quota_exhausted_logs_failure(store, source_fixture):
    platform = FakePlatform(raise_on_fetch_new=QuotaExhausted("daily limit"))
    result = await orchestrate_crawl(source_fixture, platform, store)

    assert result.status == "failed"
    assert result.error == "quota_exhausted"

    logs = store.list_crawl_log(source_id=source_fixture.id)
    assert logs[0].status == "failed"
    assert "quota_exhausted" in (logs[0].error or "")

    s = store.get_source(source_fixture.id)
    assert s.last_error == "quota_exhausted"


@pytest.mark.asyncio
async def test_channel_not_found_deactivates_source(store, source_fixture):
    platform = FakePlatform(raise_on_fetch_new=ChannelNotFound("404"))
    result = await orchestrate_crawl(source_fixture, platform, store)

    assert result.status == "failed"
    s = store.get_source(source_fixture.id)
    assert s.is_active is False
    assert "channel_not_found" in (s.last_error or "")


@pytest.mark.asyncio
async def test_unexpected_error_is_caught(store, source_fixture):
    platform = FakePlatform(raise_on_fetch_new=RuntimeError("kaboom"))
    result = await orchestrate_crawl(source_fixture, platform, store)

    assert result.status == "failed"
    assert "kaboom" in (result.error or "")


@pytest.mark.asyncio
async def test_is_short_propagated_from_video_meta(store, source_fixture):
    """Crawler должен сохранить is_short из VideoMeta (случай Instagram/TikTok)."""
    now = datetime.now(timezone.utc)
    new_videos = [
        VideoMeta(
            external_id="short1",
            url="u",
            title="T",
            published_at=_iso(now - timedelta(hours=1)),
            duration_sec=25,
            is_short=True,
        ),
    ]
    platform = FakePlatform(new_videos=new_videos)
    await orchestrate_crawl(source_fixture, platform, store)
    v = store.list_videos(source_fixture.id)[0]
    assert v.is_short is True
    assert v.duration_sec == 25


@pytest.mark.asyncio
async def test_duration_updated_from_metrics(store, source_fixture):
    """Если метрики приходят с duration (YouTube), crawler должен обновить видео."""
    now = datetime.now(timezone.utc)
    new_videos = [
        VideoMeta(
            external_id="v1",
            url="u",
            title="T",
            published_at=_iso(now - timedelta(hours=1)),
            # Обратите внимание: duration_sec отсутствует на первом проходе
        ),
    ]
    metrics = [
        MetricsSnapshot(
            external_id="v1",
            views=1000,
            likes=10,
            comments=1,
            duration_sec=42,
            is_short=True,
        ),
    ]
    platform = FakePlatform(new_videos=new_videos, metrics=metrics)
    await orchestrate_crawl(source_fixture, platform, store)
    v = store.list_videos(source_fixture.id)[0]
    assert v.duration_sec == 42
    assert v.is_short is True


@pytest.mark.asyncio
async def test_instagram_platform_uses_empty_extra(store):
    """Для не-youtube crawler не должен строить uploads_playlist_id из external_id."""
    src = store.create_source(
        account_id="acc1",
        platform="instagram",
        channel_url="https://www.instagram.com/someuser/",
        external_id="someuser",  # handle, не UC...
        channel_name="Some User",
    )
    now = datetime.now(timezone.utc)
    new_videos = [
        VideoMeta(
            external_id="post1",
            url="https://www.instagram.com/p/post1/",
            title="Caption",
            published_at=_iso(now - timedelta(hours=2)),
            duration_sec=20,
            is_short=True,
        ),
    ]
    metrics = [
        MetricsSnapshot(
            external_id="post1", views=1000, likes=100, comments=10
        ),
    ]

    class IgFake(FakePlatform):
        name = "instagram"

    platform = IgFake(new_videos=new_videos, metrics=metrics)
    result = await orchestrate_crawl(src, platform, store)
    assert result.status == "ok"
    assert result.videos_new == 1
    v = store.list_videos(src.id)[0]
    assert v.platform == "instagram"
    assert v.is_short is True
