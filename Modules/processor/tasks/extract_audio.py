import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from logging_setup import get_logger

log = get_logger("tasks.extract_audio")


@dataclass
class AudioResult:
    path: Path
    duration_sec: float
    sample_rate: int


async def probe_duration(video_path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe not found in PATH")
    proc = await asyncio.create_subprocess_exec(
        ffprobe,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(video_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {err.decode(errors='replace')}")
    data = json.loads(out.decode())
    return float(data.get("format", {}).get("duration", 0))


async def extract_audio(video_path: Path, out_path: Path, sample_rate: int = 16000) -> AudioResult:
    """Извлечение аудио: 16kHz mono mp3. Возвращает путь и длительность."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    duration = await probe_duration(video_path)

    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-y",
        "-i", str(video_path),
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-c:a", "libmp3lame",
        "-q:a", "4",
        str(out_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {err.decode(errors='replace')[-500:]}")

    log.info("audio_extracted", video=str(video_path), audio=str(out_path), duration_sec=duration)
    return AudioResult(path=out_path, duration_sec=duration, sample_rate=sample_rate)
