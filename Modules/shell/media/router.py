"""Static-media endpoints — serve processor's frame thumbnails / audio safely.

Files live in `/media/frames/{job_id}/frame_NNN.jpg` and `/media/audio/{job_id}.mp3`.
Shell mounts `/media` read-only and serves them through whitelisted endpoints
with strict path validation (no path-traversal, no arbitrary directory listing).
"""
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from orchestrator.config import get_orchestrator_settings
from orchestrator.logging_setup import get_logger

log = get_logger("media")

router = APIRouter(prefix="/api/media", tags=["media"])

# Job IDs in our system are uuid4().hex — exactly 32 lowercase hex chars
_JOB_ID_RE = re.compile(r"^[a-f0-9]{32}$")
# Frame filenames: frame_001.jpg .. frame_999.jpg (3-digit zero-padded)
_FRAME_NAME_RE = re.compile(r"^frame_\d{3}\.jpg$")


def _resolve_safe(base: Path, *parts: str) -> Path:
    """Resolve `base / *parts` and ensure the result stays inside `base`.

    Defense-in-depth: even though we validate parts via regex above, we
    re-check the resolved real path is still under the media root.
    """
    target = (base.joinpath(*parts)).resolve()
    base_resolved = base.resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_path") from exc
    return target


@router.get("/frames/{job_id}/{filename}")
async def get_frame(job_id: str, filename: str):
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_job_id")
    if not _FRAME_NAME_RE.match(filename):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_filename")

    media_dir = get_orchestrator_settings().media_dir
    target = _resolve_safe(media_dir, "frames", job_id, filename)
    if not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "frame_not_found")

    return FileResponse(
        target,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


@router.get("/audio/{job_id}.mp3")
async def get_audio(job_id: str):
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_job_id")

    media_dir = get_orchestrator_settings().media_dir
    target = _resolve_safe(media_dir, "audio", f"{job_id}.mp3")
    if not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "audio_not_found")

    return FileResponse(
        target,
        media_type="audio/mpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )
