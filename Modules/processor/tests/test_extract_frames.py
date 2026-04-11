"""Тесты dedup-логики extract_frames.

_diff_ratio и dedup тестируются через прямые вызовы на синтетических
numpy-массивах. Полный end-to-end через ffmpeg тестируется в test_transcribe
(где уже есть хелпер _make_tiny_mp4) — здесь только unit.
"""
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

HAS_FFMPEG = shutil.which("ffmpeg") is not None

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_diff_ratio_identical_frames():
    from tasks.extract_frames import _diff_ratio

    a = np.full((100, 100), 128, dtype=np.uint8)
    assert _diff_ratio(a, a.copy()) == 0.0


def test_diff_ratio_opposite_frames():
    from tasks.extract_frames import _diff_ratio

    black = np.zeros((100, 100), dtype=np.uint8)
    white = np.full((100, 100), 255, dtype=np.uint8)
    ratio = _diff_ratio(black, white)
    assert 0.99 <= ratio <= 1.00


def test_diff_ratio_small_change():
    from tasks.extract_frames import _diff_ratio

    a = np.full((100, 100), 100, dtype=np.uint8)
    b = a.copy()
    b[:10, :10] = 200  # изменили ~1% пикселей на ~40%
    ratio = _diff_ratio(a, b)
    assert 0.001 < ratio < 0.05


def test_resize_keep_downscales():
    from tasks.extract_frames import _resize_keep

    img = np.zeros((720, 2560, 3), dtype=np.uint8)
    out = _resize_keep(img, 1280)
    assert out.shape[1] == 1280
    assert out.shape[0] < 720


def test_resize_keep_noop_when_small():
    from tasks.extract_frames import _resize_keep

    img = np.zeros((480, 640, 3), dtype=np.uint8)
    out = _resize_keep(img, 1280)
    assert out.shape == (480, 640, 3)


def _make_solid_video(path: Path, duration: int, colors: list[tuple[int, int, int]]) -> None:
    """Склеивает N видео одинаковой длительности разных цветов в одно."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Альтернатива concat: используем lavfi + aevalsrc не нужен
    # Сделаем попроще: 1 цвет — ffmpeg -f lavfi color
    # Для смены цветов используем expression в drawbox, но проще сделать concat
    parts = []
    for i, (b, g, r) in enumerate(colors):
        hex_color = f"0x{r:02X}{g:02X}{b:02X}"
        part = path.parent / f"part_{i}.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", f"color=c={hex_color}:s=64x64:d={duration}:r=1",
                "-pix_fmt", "yuv420p",
                str(part),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        parts.append(part)

    # concat через demuxer
    list_file = path.parent / "concat.txt"
    list_file.write_text("\n".join(f"file '{p.name}'" for p in parts))
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=path.parent,
    )


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not in PATH")
def test_extract_frames_dedup_on_color_changes(tmp_path):
    from tasks.extract_frames import extract_frames

    video = tmp_path / "colors.mp4"
    _make_solid_video(video, duration=2, colors=[(0, 0, 0), (255, 255, 255), (0, 255, 0)])
    out_dir = tmp_path / "out"

    result = asyncio.run(
        extract_frames(
            video_path=video,
            out_dir=out_dir,
            fps=1,
            diff_threshold=0.10,
            min_frames=1,
            max_frames=40,
        )
    )
    # 3 цвета x 2 сек = 6 сырых кадров. Три сцены -> должно остаться ~3-4.
    assert result.stats["raw_count"] >= 5
    assert 2 <= result.stats["kept_count"] <= 5
    # Все сохранённые — реально существуют
    for f in result.extracted:
        assert Path(f.file_path).exists()


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not in PATH")
def test_extract_frames_static_video_min_frames(tmp_path):
    """Полностью одинаковый (один цвет) ролик -> min_frames кадров."""
    from tasks.extract_frames import extract_frames

    video = tmp_path / "static.mp4"
    _make_solid_video(video, duration=5, colors=[(50, 50, 50)])
    out_dir = tmp_path / "out"

    result = asyncio.run(
        extract_frames(
            video_path=video,
            out_dir=out_dir,
            fps=1,
            diff_threshold=0.10,
            min_frames=3,
            max_frames=40,
        )
    )
    assert result.stats["raw_count"] >= 4
    # Статичное видео: первый кадр всегда сохраняется + добор до min_frames
    assert result.stats["kept_count"] >= 3


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not in PATH")
def test_extract_frames_max_frames_limit(tmp_path):
    from tasks.extract_frames import extract_frames

    video = tmp_path / "long.mp4"
    colors = [(i * 30 % 256, 0, 0) for i in range(10)]  # 10 разных сцен
    _make_solid_video(video, duration=1, colors=colors)
    out_dir = tmp_path / "out"

    result = asyncio.run(
        extract_frames(
            video_path=video,
            out_dir=out_dir,
            fps=1,
            diff_threshold=0.05,
            min_frames=1,
            max_frames=4,
        )
    )
    assert result.stats["kept_count"] <= 4
