"""HTTP API сервиса montage (prefix /montage).

Один эндпоинт создаёт джоб (multipart: исходник + параметры) — воркер потом
рендерит его по одному в ночном окне. Владелец берётся из заголовка
X-Account-Id, который проставляет shell-прокси (он держит worker-токен). Чужой
джоб — 404 (как reels/get_run), не 403.
"""
from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from auth import account_from_header, require_worker_token
from config import get_settings
from logging_setup import get_logger
from schemas import JobOut
from state import state

log = get_logger()

router = APIRouter(
    prefix="/montage",
    tags=["montage"],
    dependencies=[Depends(require_worker_token)],
)

_ALLOWED_MODELS = {"tiny", "base", "small", "medium", "large-v3"}
_ALLOWED_VIDEO_CT = {
    "video/mp4", "video/quicktime", "video/webm", "application/octet-stream",
}
_EXT_BY_CT = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm"}
_CHUNK = 1024 * 1024
_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def _store():
    return state.job_store


def _owned_or_404(job_id: str, account_id: str | None) -> dict[str, Any]:
    job = _store().get(job_id)
    if job is None or (account_id is not None and job.get("account_id") != account_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="job_not_found")
    return job


def _validate_params(model: str | None, width: int | None, height: int | None) -> tuple[str, int, int]:
    settings = get_settings()
    model = (model or settings.default_model).strip()
    if model not in _ALLOWED_MODELS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"bad_model: {model}")
    w = int(width or settings.default_width)
    h = int(height or settings.default_height)
    if not (480 <= w <= 2160 and 854 <= h <= 3840):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="bad_dimensions")
    return model, w, h


# ── создание джоба ─────────────────────────────────────────────────────────────
@router.post("/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(
    source: UploadFile = File(...),
    title: str | None = Form(default=None),
    model: str | None = Form(default=None),
    width: int | None = Form(default=None),
    height: int | None = Form(default=None),
    language: str | None = Form(default=None),
    force: bool = Form(default=False),
    account_id: str | None = Depends(account_from_header),
):
    settings = get_settings()

    ct = (source.content_type or "").lower()
    if ct and ct not in _ALLOWED_VIDEO_CT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"unsupported_type: {ct}")

    model, w, h = _validate_params(model, width, height)

    jid = uuid.uuid4().hex
    job_root = settings.jobs_dir / jid
    src_dir = job_root / "src"
    out_dir = job_root / "out"
    src_dir.mkdir(parents=True, exist_ok=True)

    ext = _EXT_BY_CT.get(ct, Path(source.filename or "").suffix.lower() or ".mp4")
    src_name = f"source{ext}"
    src_path = src_dir / src_name

    max_bytes = settings.max_upload_mb * 1024 * 1024
    size = 0
    try:
        with open(src_path, "wb") as out:
            while chunk := await source.read(_CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"file_too_large: max {settings.max_upload_mb}MB",
                    )
                out.write(chunk)
    except HTTPException:
        shutil.rmtree(job_root, ignore_errors=True)
        raise
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(job_root, ignore_errors=True)
        log.warning("source_write_failed", job_id=jid, error=str(e))
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="write_failed")

    job = _store().create(
        job_id=jid,
        account_id=account_id,
        title=(title or None),
        source_filename=source.filename or src_name,
        source_path=str(src_path),
        out_dir=str(out_dir),
        model_size=model,
        width=w,
        height=h,
        language=(language or settings.default_language) or None,
        force=bool(force),
    )
    log.info("job_created", job_id=jid, account_id=account_id, size=size,
             model=model, wh=f"{w}x{h}", force=bool(force))
    return JobOut.from_row(job)


class FromPathReq(BaseModel):
    """Создание джоба из уже загруженного файла (shell стримит сырьё в общий том
    montage_data/incoming, чтобы не буферить большой аплоад в памяти)."""
    source_path: str
    source_filename: str | None = None
    title: str | None = None
    model: str | None = None
    width: int | None = None
    height: int | None = None
    language: str | None = None
    force: bool = False


@router.post("/jobs/from-path", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job_from_path(
    req: FromPathReq, account_id: str | None = Depends(account_from_header)
):
    settings = get_settings()
    # Guard от path-traversal: источник обязан лежать ВНУТРИ work_dir.
    try:
        src = Path(req.source_path).resolve()
        work = settings.work_dir.resolve()
        src.relative_to(work)
    except (ValueError, OSError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="source_path_outside_work_dir")
    if not src.is_file():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="source_not_found")

    model, w, h = _validate_params(req.model, req.width, req.height)

    jid = uuid.uuid4().hex
    job_root = settings.jobs_dir / jid
    src_dir = job_root / "src"
    out_dir = job_root / "out"
    src_dir.mkdir(parents=True, exist_ok=True)

    ext = src.suffix.lower() or ".mp4"
    dest = src_dir / f"source{ext}"
    try:
        shutil.move(str(src), str(dest))   # incoming → job-dir (пустеет incoming)
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(job_root, ignore_errors=True)
        log.warning("source_move_failed", job_id=jid, error=str(e))
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="move_failed")

    job = _store().create(
        job_id=jid,
        account_id=account_id,
        title=(req.title or None),
        source_filename=req.source_filename or dest.name,
        source_path=str(dest),
        out_dir=str(out_dir),
        model_size=model,
        width=w,
        height=h,
        language=(req.language or settings.default_language) or None,
        force=bool(req.force),
    )
    log.info("job_created_from_path", job_id=jid, account_id=account_id,
             model=model, wh=f"{w}x{h}", force=bool(req.force))
    return JobOut.from_row(job)


# ── список / карточка ──────────────────────────────────────────────────────────
@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=1000),
    account_id: str | None = Depends(account_from_header),
):
    rows = _store().list(account_id=account_id, status=status_filter, limit=limit)
    return [JobOut.from_row(r) for r in rows]


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: str, account_id: str | None = Depends(account_from_header)):
    return JobOut.from_row(_owned_or_404(job_id, account_id))


@router.get("/jobs/{job_id}/result")
async def get_job_result(job_id: str, account_id: str | None = Depends(account_from_header)):
    job = _owned_or_404(job_id, account_id)
    rp = job.get("result_path")
    if job.get("status") != "done" or not rp or not Path(rp).is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="result_not_ready")
    return FileResponse(rp, media_type="video/mp4", filename=f"{job_id}.mp4")


@router.get("/jobs/{job_id}/log", response_class=PlainTextResponse)
async def get_job_log(job_id: str, account_id: str | None = Depends(account_from_header)):
    job = _owned_or_404(job_id, account_id)
    log_file = Path(job.get("out_dir") or "") / "run.log"
    if log_file.is_file():
        return PlainTextResponse(log_file.read_text(encoding="utf-8", errors="replace"))
    return PlainTextResponse(job.get("log_tail") or "")


@router.delete("/jobs/{job_id}", status_code=status.HTTP_200_OK)
async def delete_job(job_id: str, account_id: str | None = Depends(account_from_header)):
    job = _owned_or_404(job_id, account_id)
    if job.get("status") == "running":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="job_running")
    if not _ID_RE.match(job_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="bad_id")
    shutil.rmtree(get_settings().jobs_dir / job_id, ignore_errors=True)
    deleted = _store().delete(job_id)
    log.info("job_deleted", job_id=job_id, ok=deleted)
    return {"deleted": deleted}
