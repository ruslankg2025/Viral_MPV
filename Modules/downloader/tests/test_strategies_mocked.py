"""Юнит-тесты стратегий с моками сетевых вызовов (Apify, yt-dlp, httpx)."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from strategies.instagram import InstagramDownloader
from strategies.tiktok import TikTokDownloader
from strategies.youtube import YouTubeShortsDownloader


def _make_fake_mp4(target: Path, content: bytes = b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 1024) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


# ----------------- Instagram -----------------

def test_ig_apify_path(monkeypatch, tmp_path):
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    monkeypatch.setenv("STUB_MODE", "false")
    from config import get_settings
    get_settings.cache_clear()

    apify_resp = [{"videoUrl": "https://cdn.example.com/reel.mp4"}]

    async def fake_run_actor(**kw):
        return apify_resp

    async def fake_stream(url, output_path, *, timeout_sec=120):
        _make_fake_mp4(output_path)
        return output_path.stat().st_size

    with patch("strategies.instagram.run_actor_sync", new=fake_run_actor), \
         patch("strategies.instagram.stream_download", new=fake_stream):
        strat = InstagramDownloader()
        result = asyncio.run(
            strat.download(
                "https://www.instagram.com/reel/CTEST/",
                downloads_dir=tmp_path,
                quality="720p",
            )
        )

    assert result.file_path == (tmp_path / "ig_CTEST.mp4").resolve()
    assert result.file_path.exists()
    assert result.platform == "instagram"
    assert result.external_id == "CTEST"
    assert result.strategy_used == "apify"
    assert result.sha256 and len(result.sha256) == 64
    assert result.size_bytes > 0


def test_ig_apify_no_video_url_falls_back_to_ytdlp(monkeypatch, tmp_path):
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    monkeypatch.setenv("STUB_MODE", "false")
    from config import get_settings
    get_settings.cache_clear()

    async def fake_run_actor(**kw):
        return [{"videoUrl": None}]  # видео-URL отсутствует

    async def fake_ytdlp(url, output_path, *, format_str, cookies_file=None):
        _make_fake_mp4(output_path)
        return {"duration_sec": 23.4, "width": 1080, "height": 1920}

    with patch("strategies.instagram.run_actor_sync", new=fake_run_actor), \
         patch("strategies.instagram.download_with_ytdlp", new=fake_ytdlp):
        strat = InstagramDownloader()
        result = asyncio.run(
            strat.download(
                "https://www.instagram.com/reel/CXYZ/",
                downloads_dir=tmp_path,
                quality="720p",
            )
        )
    assert result.strategy_used == "yt_dlp"
    assert result.external_id == "CXYZ"


def test_ig_cached_skip_network(monkeypatch, tmp_path):
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    monkeypatch.setenv("STUB_MODE", "false")
    from config import get_settings
    get_settings.cache_clear()

    # Файл уже на диске
    cached = tmp_path / "ig_CACHED.mp4"
    _make_fake_mp4(cached)

    # Ни Apify, ни yt-dlp не должны вызваться → ставим explicit AsyncMock с побочкой
    apify_mock = AsyncMock(side_effect=AssertionError("apify must not be called"))
    ytdlp_mock = AsyncMock(side_effect=AssertionError("ytdlp must not be called"))

    with patch("strategies.instagram.run_actor_sync", apify_mock), \
         patch("strategies.instagram.download_with_ytdlp", ytdlp_mock):
        strat = InstagramDownloader()
        result = asyncio.run(
            strat.download(
                "https://www.instagram.com/reel/CACHED/",
                downloads_dir=tmp_path,
                quality="720p",
            )
        )

    assert result.strategy_used == "cached"
    assert result.file_path == cached.resolve()
    apify_mock.assert_not_awaited()
    ytdlp_mock.assert_not_awaited()


# ----------------- TikTok -----------------

def test_tt_ytdlp_path(monkeypatch, tmp_path):
    monkeypatch.setenv("STUB_MODE", "false")
    from config import get_settings
    get_settings.cache_clear()

    async def fake_ytdlp(url, output_path, *, format_str, cookies_file=None):
        _make_fake_mp4(output_path)
        return {"duration_sec": 12.0, "width": 720, "height": 1280}

    with patch("strategies.tiktok.download_with_ytdlp", new=fake_ytdlp):
        strat = TikTokDownloader()
        result = asyncio.run(
            strat.download(
                "https://www.tiktok.com/@user/video/7012345678901234567",
                downloads_dir=tmp_path,
                quality="720p",
            )
        )
    assert result.strategy_used == "yt_dlp"
    assert result.external_id == "7012345678901234567"
    assert result.platform == "tiktok"


def test_tt_ytdlp_fail_then_apify(monkeypatch, tmp_path):
    monkeypatch.setenv("APIFY_TOKEN", "test-token")
    monkeypatch.setenv("STUB_MODE", "false")
    from config import get_settings
    get_settings.cache_clear()

    from strategies.ytdlp_helper import YtDlpError

    async def fake_ytdlp_fail(url, output_path, *, format_str, cookies_file=None):
        raise YtDlpError("simulated_403")

    async def fake_run_actor(**kw):
        return [{"videoMeta": {"downloadAddr": "https://cdn.example.com/tt.mp4"}}]

    async def fake_stream(url, output_path, *, timeout_sec=120):
        _make_fake_mp4(output_path)
        return output_path.stat().st_size

    with patch("strategies.tiktok.download_with_ytdlp", new=fake_ytdlp_fail), \
         patch("strategies.tiktok.run_actor_sync", new=fake_run_actor), \
         patch("strategies.tiktok.stream_download", new=fake_stream):
        strat = TikTokDownloader()
        result = asyncio.run(
            strat.download(
                "https://www.tiktok.com/@u/video/123",
                downloads_dir=tmp_path,
                quality="720p",
            )
        )
    assert result.strategy_used == "apify"


# ----------------- YouTube Shorts -----------------

def test_yt_ytdlp_ok(monkeypatch, tmp_path):
    monkeypatch.setenv("STUB_MODE", "false")
    from config import get_settings
    get_settings.cache_clear()

    async def fake_ytdlp(url, output_path, *, format_str, cookies_file=None):
        _make_fake_mp4(output_path)
        return {"duration_sec": 25.0, "width": 1080, "height": 1920}

    with patch("strategies.youtube.download_with_ytdlp", new=fake_ytdlp):
        strat = YouTubeShortsDownloader()
        result = asyncio.run(
            strat.download(
                "https://www.youtube.com/shorts/abc123XYZ",
                downloads_dir=tmp_path,
                quality="720p",
            )
        )
    assert result.strategy_used == "yt_dlp"
    assert result.external_id == "abc123XYZ"


def test_yt_long_video_rejected(monkeypatch, tmp_path):
    """Если ffprobe вернёт duration > 60с — это не Short, файл удаляется, ошибка."""
    monkeypatch.setenv("STUB_MODE", "false")
    from config import get_settings
    get_settings.cache_clear()

    async def fake_ytdlp(url, output_path, *, format_str, cookies_file=None):
        _make_fake_mp4(output_path)
        return {"duration_sec": 120.0, "width": 1920, "height": 1080}

    # ffprobe вернёт нашу синтетическую длительность через моk
    def fake_probe(path):
        return {"duration_sec": 120.0, "width": 1920, "height": 1080}

    with patch("strategies.youtube.download_with_ytdlp", new=fake_ytdlp), \
         patch("strategies.youtube.ffprobe_meta", new=fake_probe):
        strat = YouTubeShortsDownloader()
        with pytest.raises(RuntimeError, match="not_a_short"):
            asyncio.run(
                strat.download(
                    "https://www.youtube.com/shorts/longvid",
                    downloads_dir=tmp_path,
                    quality="720p",
                )
            )

    # Файл должен быть удалён
    assert not (tmp_path / "yt_longvid.mp4").exists()
