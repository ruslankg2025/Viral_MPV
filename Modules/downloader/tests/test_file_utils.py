import asyncio
from pathlib import Path

import pytest

from strategies.file_utils import atomic_download, sha256_of


def test_sha256_of(tmp_path: Path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello world")
    # Pre-computed: sha256("hello world")
    assert sha256_of(f) == (
        "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    )


def test_atomic_download_writes_and_renames(tmp_path: Path):
    target = tmp_path / "out" / "file.mp4"

    async def writer(tmp: Path) -> None:
        tmp.write_bytes(b"\x00\x01\x02 video bytes")

    result = asyncio.run(atomic_download(target, writer))
    assert result is True
    assert target.exists()
    assert target.read_bytes() == b"\x00\x01\x02 video bytes"

    # Никаких временных .tmp_* файлов не остаётся
    leftovers = list(target.parent.glob(".tmp_*"))
    assert leftovers == []


def test_atomic_download_propagates_writer_error(tmp_path: Path):
    target = tmp_path / "file.mp4"

    async def failing_writer(tmp: Path) -> None:
        raise RuntimeError("simulated_io_failure")

    with pytest.raises(RuntimeError, match="simulated_io_failure"):
        asyncio.run(atomic_download(target, failing_writer))

    # target не создан, временный файл удалён
    assert not target.exists()
    assert list(target.parent.glob(".tmp_*")) == []


def test_atomic_download_concurrent_safe(tmp_path: Path):
    """Два конкурентных atomic_download того же target — оба завершаются OK,
    итоговый файл содержит данные от одного из них."""
    target = tmp_path / "out.mp4"

    async def writer_a(tmp: Path) -> None:
        await asyncio.sleep(0.01)
        tmp.write_bytes(b"AAA")

    async def writer_b(tmp: Path) -> None:
        await asyncio.sleep(0.005)
        tmp.write_bytes(b"BBB")

    async def main():
        return await asyncio.gather(
            atomic_download(target, writer_a),
            atomic_download(target, writer_b),
        )

    results = asyncio.run(main())
    # Оба завершились без исключений
    assert all(isinstance(r, bool) for r in results)
    # Файл существует и содержит ровно одно из значений
    assert target.exists()
    assert target.read_bytes() in (b"AAA", b"BBB")
    # Никаких temp-leftover-ов
    assert list(target.parent.glob(".tmp_*")) == []
