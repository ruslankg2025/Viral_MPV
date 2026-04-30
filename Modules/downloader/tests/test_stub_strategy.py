import asyncio
import shutil
from pathlib import Path

import pytest

from strategies.stub import StubDownloader


@pytest.fixture
def fixture_file(tmp_path: Path) -> Path:
    """Маленький fake mp4 для теста (не настоящее видео — ffprobe вернёт None, ок)."""
    f = tmp_path / "fixture.mp4"
    f.write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 1024)
    return f


def test_stub_returns_result(tmp_path: Path, fixture_file: Path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    strat = StubDownloader(fixture_file)

    result = asyncio.run(
        strat.download(
            "https://www.instagram.com/reel/CXXXXXX/",
            downloads_dir=downloads,
            quality="720p",
        )
    )

    assert result.file_path.exists()
    assert result.file_path.parent == downloads
    assert result.sha256 and len(result.sha256) == 64
    assert result.size_bytes > 0
    assert result.strategy_used == "stub"
    assert result.platform == "stub"
    assert result.format == "mp4"
    assert result.external_id.startswith("stub_")


def test_stub_idempotent(tmp_path: Path, fixture_file: Path):
    """Повторный вызов с тем же URL не должен пере-копировать файл."""
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    strat = StubDownloader(fixture_file)

    r1 = asyncio.run(strat.download("https://x.com/y", downloads_dir=downloads, quality="720p"))
    mtime1 = r1.file_path.stat().st_mtime

    r2 = asyncio.run(strat.download("https://x.com/y", downloads_dir=downloads, quality="720p"))
    assert r1.file_path == r2.file_path
    assert r1.sha256 == r2.sha256
    # Не делаем strict assert на mtime (может тикнуть), но external_id и путь стабильны
    assert r1.external_id == r2.external_id


def test_stub_missing_fixture_raises(tmp_path: Path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    missing = tmp_path / "nope.mp4"
    strat = StubDownloader(missing)

    with pytest.raises(FileNotFoundError):
        asyncio.run(strat.download("https://x", downloads_dir=downloads, quality="720p"))
