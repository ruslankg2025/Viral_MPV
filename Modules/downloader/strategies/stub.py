import asyncio
import hashlib
import shutil
from pathlib import Path

from logging_setup import get_logger
from strategies.base import BaseDownloader, DownloadResult

log = get_logger("strategies.stub")


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _ffprobe_meta(path: Path) -> dict:
    """Лёгкий ffprobe-вызов; при отсутствии ffprobe возвращает пустоту."""
    try:
        import ffmpeg

        probe = ffmpeg.probe(str(path))
        v_streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
        v = v_streams[0] if v_streams else {}
        fmt = probe.get("format", {})
        return {
            "duration_sec": float(fmt.get("duration")) if fmt.get("duration") else None,
            "width": int(v.get("width")) if v.get("width") else None,
            "height": int(v.get("height")) if v.get("height") else None,
        }
    except Exception as e:
        log.warning("ffprobe_failed", error=str(e))
        return {"duration_sec": None, "width": None, "height": None}


class StubDownloader(BaseDownloader):
    """Возвращает фиксированный fixture-файл вместо реального скачивания.

    Используется в STUB_MODE=true (этап 1) — позволяет разрабатывать orchestrator
    и UI без реальных стратегий.
    """

    name = "stub"

    def __init__(self, fixture_path: Path):
        self.fixture_path = fixture_path

    async def download(
        self, url: str, *, downloads_dir: Path, quality: str
    ) -> DownloadResult:
        if not self.fixture_path.exists():
            raise FileNotFoundError(
                f"stub fixture not found at {self.fixture_path}; "
                f"STUB_MODE требует наличия файла"
            )

        # external_id для stub-режима — детерминированный slug из URL
        external_id = "stub_" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
        target = downloads_dir / f"{external_id}.mp4"

        # Копируем фикстуру (idempotent: если файл уже есть с тем же sha — переиспользуем)
        if not target.exists():
            await asyncio.to_thread(shutil.copy2, self.fixture_path, target)

        sha = await asyncio.to_thread(_sha256_of, target)
        meta = await asyncio.to_thread(_ffprobe_meta, target)

        log.info(
            "stub_download_ok",
            url=url,
            file=str(target),
            size=target.stat().st_size,
        )

        return DownloadResult(
            file_path=target,
            external_id=external_id,
            platform="stub",
            duration_sec=meta["duration_sec"],
            width=meta["width"],
            height=meta["height"],
            size_bytes=target.stat().st_size,
            sha256=sha,
            format="mp4",
            strategy_used="stub",
        )
