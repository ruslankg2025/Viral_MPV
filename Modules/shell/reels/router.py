"""Готовые ролики (вкладка «Ролики») — загрузка авторского MP4.

Файлы: REELS_DIR/{id}/video.<ext> + thumb.jpg (превью генерит браузер через
canvas — в контейнере shell нет ffmpeg). Метаданные: REELS_DB (SQLite на том
shell_db, который writable; /media смонтирован read-only).

Владелец берётся ТОЛЬКО из сессии/сервисного токена (require_auth). Чужой
ролик — 404, как в get_run (не 403, чтобы перебором нельзя было подтвердить
существование).
"""
import os
import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse

from auth.deps import AuthContext, require_auth
from orchestrator.logging_setup import get_logger
from reels.store import ReelStore

log = get_logger("reels")

router = APIRouter(prefix="/api/orchestrator/reels", tags=["reels"])

# Лимит размера видео. nginx тоже ограничивает тело (client_max_body_size) —
# держим их согласованными.
MAX_VIDEO_BYTES = 300 * 1024 * 1024  # 300 MB
_CHUNK = 1024 * 1024  # 1 MB
_ALLOWED_VIDEO_CT = {"video/mp4", "video/quicktime", "video/webm", "application/octet-stream"}
_EXT_BY_CT = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm"}

_store: ReelStore | None = None


def _reels_dir() -> Path:
    return Path(os.getenv("REELS_DIR", "/uploads"))


def _get_store() -> ReelStore:
    global _store
    if _store is None:
        db_path = Path(os.getenv("REELS_DB", "/db/reels.db"))
        _store = ReelStore(db_path)
    return _store


def _owned_or_404(reel_id: str, auth: AuthContext) -> dict[str, Any]:
    reel = _get_store().get(reel_id)
    if reel is None or (auth.enforce and reel.get("account_id") != auth.account_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="reel_not_found")
    return reel


def _public(reel: dict[str, Any]) -> dict[str, Any]:
    """Мета для фронта — без внутренних имён файлов, с URL для тега <img>/<video>."""
    rid = reel["id"]
    return {
        "id": rid,
        "title": reel.get("title") or "",
        "note": reel.get("note") or "",
        "size_bytes": reel.get("size_bytes"),
        "duration_sec": reel.get("duration_sec"),
        "created_at": reel.get("created_at"),
        "video_url": f"/api/orchestrator/reels/{rid}/video",
        "thumb_url": f"/api/orchestrator/reels/{rid}/thumb" if reel.get("thumb_filename") else None,
    }


@router.get("")
async def list_reels(auth: AuthContext = Depends(require_auth)):  # noqa: B008
    acct = auth.account_id if auth.enforce else None
    return [_public(r) for r in _get_store().list_by_account(acct)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_reel(
    video: UploadFile = File(...),
    title: str = Form(...),
    note: str | None = Form(default=None),
    thumb: UploadFile | None = File(default=None),
    duration_sec: float | None = Form(default=None),
    auth: AuthContext = Depends(require_auth),  # noqa: B008
):
    title = (title or "").strip()
    if not title:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="title_required")
    ct = (video.content_type or "").lower()
    if ct and ct not in _ALLOWED_VIDEO_CT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"unsupported_type: {ct}")

    store = _get_store()
    reel_id = os.urandom(16).hex()
    dest_dir = _reels_dir() / reel_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    ext = _EXT_BY_CT.get(ct, ".mp4")
    video_name = f"video{ext}"
    video_path = dest_dir / video_name

    # Стримим файл на диск чанками (не грузим целиком в память) + лимит размера.
    size = 0
    try:
        with open(video_path, "wb") as out:
            while True:
                chunk = await video.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_VIDEO_BYTES:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"file_too_large: max {MAX_VIDEO_BYTES // (1024*1024)}MB",
                    )
                out.write(chunk)
    except HTTPException:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(dest_dir, ignore_errors=True)
        log.warning("reel_upload_write_failed", reel_id=reel_id, error=str(e))
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="write_failed")

    # Превью (jpeg из браузера) — best-effort, ролик валиден и без него.
    thumb_name: str | None = None
    if thumb is not None:
        try:
            thumb_name = "thumb.jpg"
            tsize = 0
            with open(dest_dir / thumb_name, "wb") as out:
                while True:
                    chunk = await thumb.read(_CHUNK)
                    if not chunk:
                        break
                    tsize += len(chunk)
                    if tsize > 8 * 1024 * 1024:  # превью > 8MB — что-то не так, бросаем
                        break
                    out.write(chunk)
        except Exception as e:  # noqa: BLE001
            thumb_name = None
            log.info("reel_thumb_skipped", reel_id=reel_id, error=str(e)[:120])

    # id совпадает с именем папки — файлы находятся по reel.id
    reel = store.create(
        reel_id=reel_id,
        account_id=auth.account_id,
        title=title,
        note=(note or None),
        video_filename=video_name,
        thumb_filename=thumb_name,
        content_type=ct or None,
        size_bytes=size,
        duration_sec=duration_sec,
    )
    log.info("reel_uploaded", reel_id=reel_id, account_id=auth.account_id, size=size)
    return _public(reel)


@router.get("/{reel_id}/video")
async def get_reel_video(reel_id: str, auth: AuthContext = Depends(require_auth)):  # noqa: B008
    reel = _owned_or_404(reel_id, auth)
    path = _reels_dir() / reel_id / (reel.get("video_filename") or "")
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="video_missing")
    return FileResponse(path, media_type=reel.get("content_type") or "video/mp4")


@router.get("/{reel_id}/thumb")
async def get_reel_thumb(reel_id: str, auth: AuthContext = Depends(require_auth)):  # noqa: B008
    reel = _owned_or_404(reel_id, auth)
    name = reel.get("thumb_filename")
    if not name:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no_thumb")
    path = _reels_dir() / reel_id / name
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="thumb_missing")
    return FileResponse(
        path, media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.delete("/{reel_id}")
async def delete_reel(reel_id: str, auth: AuthContext = Depends(require_auth)):  # noqa: B008
    reel = _owned_or_404(reel_id, auth)
    # Защита от path-traversal: reel_id из БД — hex, но перепроверим перед rmtree.
    if not re.match(r"^[a-f0-9]{32}$", reel_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="bad_id")
    shutil.rmtree(_reels_dir() / reel_id, ignore_errors=True)
    deleted = _get_store().delete(reel_id)
    log.info("reel_deleted", reel_id=reel_id, ok=deleted)
    return {"deleted": deleted}
