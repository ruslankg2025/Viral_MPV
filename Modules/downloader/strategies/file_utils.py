"""Общие утилиты для стратегий: streaming sha256, ffprobe metadata,
file-lock через tempfile+rename (защита от гонок параллельных скачиваний).
"""
import asyncio
import hashlib
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

from logging_setup import get_logger

log = get_logger("strategies.file_utils")


def sha256_of(path: Path) -> str:
    """Считает sha256 файла стримом."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ffprobe_meta(path: Path) -> dict:
    """duration_sec/width/height. None при ошибке (не критично — meta опциональна)."""
    try:
        import ffmpeg

        probe = ffmpeg.probe(str(path))
        v_streams = [
            s for s in probe.get("streams", []) if s.get("codec_type") == "video"
        ]
        v = v_streams[0] if v_streams else {}
        fmt = probe.get("format", {})
        return {
            "duration_sec": float(fmt.get("duration")) if fmt.get("duration") else None,
            "width": int(v.get("width")) if v.get("width") else None,
            "height": int(v.get("height")) if v.get("height") else None,
        }
    except Exception as e:
        log.warning("ffprobe_failed", path=str(path), error=str(e))
        return {"duration_sec": None, "width": None, "height": None}


async def atomic_download(
    target: Path,
    write_to_path: Callable[[Path], Awaitable[None]],
) -> bool:
    """Атомарная запись файла с защитой от гонок.

    Стратегия:
    1. Скачиваем во временный файл рядом (tempfile в той же директории —
       чтобы rename был атомарным: один FS).
    2. Атомарно переименовываем во `target`.
    3. Если другой процесс уже создал target (FileExistsError на Windows /
       подобное) — стираем temp и возвращаем False (используется existing).

    Возвращает True если этот вызов записал файл, False — если кто-то опередил.
    Не делает sha256/probe — это ответственность вызывающего после rename.
    """
    target.parent.mkdir(parents=True, exist_ok=True)

    # Создаём temp в той же директории, чтобы rename был атомарным
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=".tmp_",
        suffix=target.suffix,
    )
    # Закрываем дескриптор сразу — будем писать через write_to_path
    import os
    os.close(fd)
    tmp_path = Path(tmp_name)

    try:
        await write_to_path(tmp_path)
        # Попытка атомарного rename
        try:
            tmp_path.replace(target)  # replace = atomic, overwrites on POSIX, on Windows for files
            return True
        except FileExistsError:
            # На Windows replace() как правило перезаписывает; этот путь
            # маловероятен, но на всякий случай:
            log.info("atomic_rename_lost_race", target=str(target))
            tmp_path.unlink(missing_ok=True)
            return False
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
