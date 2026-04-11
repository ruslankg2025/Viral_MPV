"""Извлечение кадров с OpenCV-дедупликацией.

Алгоритм (см. plan §2.4):
1. ffmpeg sampling: fps кадров в секунду -> временная папка raw/
2. OpenCV проход: для каждого кадра (по возрастанию timestamp) считаем
   diff_ratio = sum(|gray - last_kept_gray|) / (255 * w * h)
3. Если diff_ratio >= diff_threshold -> сохраняем в окончательную папку
4. Если kept < min_frames -> добираем из отброшенных равномерно
5. Если kept > max_frames -> равномерно прореживаем
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from logging_setup import get_logger

log = get_logger("tasks.extract_frames")


@dataclass
class FrameInfo:
    index: int
    timestamp_sec: float
    file_path: str
    diff_ratio: float


@dataclass
class FramesResult:
    extracted: list[FrameInfo] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


async def _run_ffmpeg(args: list[str]) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH")
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {err.decode(errors='replace')[-500:]}")


def _diff_ratio(a: np.ndarray, b: np.ndarray) -> float:
    """|a - b| / (255 * pixels), both grayscale uint8."""
    if a.shape != b.shape:
        h = min(a.shape[0], b.shape[0])
        w = min(a.shape[1], b.shape[1])
        a = a[:h, :w]
        b = b[:h, :w]
    diff = cv2.absdiff(a, b)
    total = float(diff.sum())
    pixels = diff.size
    if pixels == 0:
        return 0.0
    return total / (255.0 * pixels)


def _resize_keep(img: np.ndarray, max_width: int) -> np.ndarray:
    h, w = img.shape[:2]
    if w <= max_width:
        return img
    scale = max_width / w
    return cv2.resize(img, (max_width, int(h * scale)), interpolation=cv2.INTER_AREA)


async def extract_frames(
    *,
    video_path: Path,
    out_dir: Path,
    fps: float = 1.0,
    diff_threshold: float = 0.10,
    min_frames: int = 3,
    max_frames: int = 40,
    max_width: int = 1280,
    jpeg_quality: int = 85,
) -> FramesResult:
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="frames_raw_") as raw_dir_str:
        raw_dir = Path(raw_dir_str)
        pattern = str(raw_dir / "raw_%06d.jpg")

        # Extraction: -vf fps=N -q:v 3 (качество ~85)
        await _run_ffmpeg([
            "-y",
            "-i", str(video_path),
            "-vf", f"fps={fps}",
            "-q:v", "3",
            pattern,
        ])

        raw_files = sorted(raw_dir.glob("raw_*.jpg"))
        if not raw_files:
            return FramesResult(
                extracted=[],
                stats={
                    "raw_count": 0,
                    "kept_count": 0,
                    "dropped_count": 0,
                    "duration_sec": 0.0,
                },
            )

        # Dedup loop
        kept_idx: list[int] = []
        diffs: list[float] = []
        dropped_idx: list[int] = []
        last_gray: np.ndarray | None = None

        for i, f in enumerate(raw_files):
            img = cv2.imread(str(f), cv2.IMREAD_COLOR)
            if img is None:
                dropped_idx.append(i)
                diffs.append(0.0)
                continue
            img = _resize_keep(img, max_width)
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            if last_gray is None:
                ratio = 1.0  # первый кадр всегда сохраняем
            else:
                ratio = _diff_ratio(gray, last_gray)

            diffs.append(ratio)

            if ratio >= diff_threshold:
                kept_idx.append(i)
                last_gray = gray
            else:
                dropped_idx.append(i)

        # Min: добиваем из отброшенных равномерно
        if len(kept_idx) < min_frames and dropped_idx:
            need = min_frames - len(kept_idx)
            if need > 0:
                step = max(1, len(dropped_idx) // need)
                added = [dropped_idx[i] for i in range(0, len(dropped_idx), step)][:need]
                kept_idx = sorted(set(kept_idx) | set(added))

        # Max: прореживаем равномерно
        if len(kept_idx) > max_frames:
            step = len(kept_idx) / max_frames
            selected = [kept_idx[int(i * step)] for i in range(max_frames)]
            kept_idx = sorted(set(selected))

        # Сохраняем финальные кадры в out_dir
        kept_idx.sort()
        extracted: list[FrameInfo] = []
        for order, i in enumerate(kept_idx, start=1):
            src = raw_files[i]
            img = cv2.imread(str(src), cv2.IMREAD_COLOR)
            if img is None:
                continue
            img = _resize_keep(img, max_width)
            target = out_dir / f"frame_{order:03d}.jpg"
            cv2.imwrite(str(target), img, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
            timestamp = i / fps  # sampling был равномерный fps/sec
            extracted.append(
                FrameInfo(
                    index=order,
                    timestamp_sec=round(timestamp, 3),
                    file_path=str(target),
                    diff_ratio=round(diffs[i], 4),
                )
            )

        duration = (len(raw_files) - 1) / fps if len(raw_files) > 1 else 0.0

        result = FramesResult(
            extracted=extracted,
            stats={
                "raw_count": len(raw_files),
                "kept_count": len(extracted),
                "dropped_count": len(raw_files) - len(extracted),
                "duration_sec": round(duration, 3),
                "fps": fps,
                "diff_threshold": diff_threshold,
                "min_frames": min_frames,
                "max_frames": max_frames,
            },
        )
        log.info(
            "frames_extracted",
            video=str(video_path),
            raw=len(raw_files),
            kept=len(extracted),
            dropped=len(raw_files) - len(extracted),
        )
        return result


async def run_extract_frames(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from state import state

    file_path = Path(payload["file_path"])
    sampling = payload.get("sampling") or {}
    out_dir = state.settings.media_dir / "frames" / job_id

    result = await extract_frames(
        video_path=file_path,
        out_dir=out_dir,
        fps=float(sampling.get("fps", 1.0)),
        diff_threshold=float(sampling.get("diff_threshold", 0.10)),
        min_frames=int(sampling.get("min_frames", 3)),
        max_frames=int(sampling.get("max_frames", 40)),
    )

    return {
        "frames": {
            "extracted": [
                {
                    "index": f.index,
                    "timestamp_sec": f.timestamp_sec,
                    "file_path": f.file_path,
                    "diff_ratio": f.diff_ratio,
                }
                for f in result.extracted
            ],
            "stats": result.stats,
        }
    }
