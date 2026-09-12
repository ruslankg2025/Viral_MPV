"""Обложки (вкладка «Обложки») — библиотека фото + сохранённые обложки.

Рендер обложек — на КЛИЕНТЕ (canvas → PNG), сервер только хранит/отдаёт ассеты.
Все ассеты same-origin (отдаёт shell) — чтобы client-side `toBlob` не тейнтился.
Владелец только из сессии (require_auth); чужое — 404 (как reels).

В shell НЕТ Pillow — фото не декодируем: валидируем по magic-bytes, отдаём с
`X-Content-Type-Options: nosniff` и явным content-type (анти-sniff/XSS). Ресайз
фото делает клиент до загрузки.

Маршруты (prefix /api/orchestrator/covers): фото под /photos/*; обложки —
в корне (POST ""/GET ""/GET|PATCH|DELETE /{id}/…). Статические /photos/*
объявлены ДО параметрических /{id}, чтобы не перехватывались.
"""
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from auth.deps import AuthContext, require_auth
from covers.store import FORMATS, CoverStore
from orchestrator.logging_setup import get_logger

log = get_logger("covers")

router = APIRouter(prefix="/api/orchestrator/covers", tags=["covers"])

_CHUNK = 1024 * 1024
MAX_PHOTO_MB = int(os.getenv("COVERS_PHOTO_MAX_MB", "25"))
MAX_RENDER_MB = int(os.getenv("COVERS_RENDER_MAX_MB", "20"))
_EXT_BY_CT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

_store: CoverStore | None = None


def _covers_dir() -> Path:
    return Path(os.getenv("COVERS_DIR", "/uploads/covers"))


def _get_store() -> CoverStore:
    global _store
    if _store is None:
        _store = CoverStore(Path(os.getenv("COVERS_DB", "/db/covers.db")))
    return _store


def _acct(auth: AuthContext) -> str | None:
    return auth.account_id if auth.enforce else None


def _sniff_image_ct(head: bytes) -> str | None:
    """Тип по magic-bytes (клиентскому content-type не доверяем)."""
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


# ── photos (объявлены ПЕРВЫМИ — статический сегмент) ─────────────────────────
@router.post("/photos", status_code=status.HTTP_201_CREATED)
async def upload_photo(
    photo: UploadFile = File(...),
    width: int | None = Form(default=None),
    height: int | None = Form(default=None),
    auth: AuthContext = Depends(require_auth),  # noqa: B008
):
    store = _get_store()
    pid = os.urandom(16).hex()
    pdir = _covers_dir() / "photos"
    pdir.mkdir(parents=True, exist_ok=True)

    max_bytes = MAX_PHOTO_MB * 1024 * 1024
    first = await photo.read(_CHUNK)
    real_ct = _sniff_image_ct(first)
    if real_ct is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="not_an_image")
    dest = pdir / f"{pid}{_EXT_BY_CT[real_ct]}"
    size = 0
    try:
        with open(dest, "wb") as out:
            chunk = first
            while chunk:
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"file_too_large: max {MAX_PHOTO_MB}MB",
                    )
                out.write(chunk)
                chunk = await photo.read(_CHUNK)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception as e:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        log.warning("photo_write_failed", error=str(e))
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="write_failed")

    rec = store.create_photo(
        account_id=auth.account_id, photo_id=pid, filename=dest.name,
        content_type=real_ct, size_bytes=size, width=width, height=height,
    )
    log.info("photo_uploaded", photo_id=pid, account_id=auth.account_id, size=size)
    return _photo_public(rec)


@router.get("/photos")
async def list_photos(auth: AuthContext = Depends(require_auth)):  # noqa: B008
    return [_photo_public(p) for p in _get_store().list_photos(_acct(auth))]


@router.get("/photos/{photo_id}")
async def get_photo(photo_id: str, auth: AuthContext = Depends(require_auth)):  # noqa: B008
    rec = _photo_owned_or_404(photo_id, auth)
    path = _covers_dir() / "photos" / (rec.get("filename") or "")
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="photo_missing")
    return FileResponse(
        path, media_type=rec.get("content_type") or "image/jpeg",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"},
    )


@router.delete("/photos/{photo_id}")
async def delete_photo(photo_id: str, auth: AuthContext = Depends(require_auth)):  # noqa: B008
    rec = _photo_owned_or_404(photo_id, auth)
    # filename серверное ({id}{ext}) — безопасно удалять напрямую.
    (_covers_dir() / "photos" / (rec.get("filename") or "")).unlink(missing_ok=True)
    ok = _get_store().delete_photo(photo_id)
    return {"deleted": ok}


# ── covers (в корне prefix) ───────────────────────────────────────────────────
class CoverCreateReq(BaseModel):
    title: str | None = None
    run_id: str | None = None
    reel_id: str | None = None
    format: str = Field(default="9x16")
    spec: dict[str, Any] = Field(default_factory=dict)


class CoverPatchReq(BaseModel):
    title: str | None = None
    format: str | None = None
    spec: dict[str, Any] | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_cover(req: CoverCreateReq, auth: AuthContext = Depends(require_auth)):  # noqa: B008
    if req.format not in FORMATS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"bad_format: {req.format}")
    rec = _get_store().create_cover(
        account_id=auth.account_id, title=(req.title or None), run_id=req.run_id,
        reel_id=req.reel_id, fmt=req.format, spec=req.spec,
    )
    log.info("cover_created", cover_id=rec["id"], account_id=auth.account_id)
    return _cover_public(rec)


@router.get("")
async def list_covers(
    run_id: str | None = Query(default=None),
    auth: AuthContext = Depends(require_auth),  # noqa: B008
):
    return [_cover_public(c) for c in _get_store().list_covers(_acct(auth), run_id=run_id)]


@router.get("/{cover_id}")
async def get_cover(cover_id: str, auth: AuthContext = Depends(require_auth)):  # noqa: B008
    return _cover_public(_cover_owned_or_404(cover_id, auth))


@router.patch("/{cover_id}")
async def patch_cover(
    cover_id: str, req: CoverPatchReq, auth: AuthContext = Depends(require_auth),  # noqa: B008
):
    _cover_owned_or_404(cover_id, auth)
    if req.format is not None and req.format not in FORMATS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"bad_format: {req.format}")
    rec = _get_store().update_cover(cover_id, title=req.title, fmt=req.format, spec=req.spec)
    return _cover_public(rec)


@router.post("/{cover_id}/render", status_code=status.HTTP_201_CREATED)
async def upload_render(
    cover_id: str,
    image: UploadFile = File(...),
    format: str = Form(...),
    auth: AuthContext = Depends(require_auth),  # noqa: B008
):
    """Клиент отрисовал обложку в canvas и прислал готовый PNG для формата."""
    _cover_owned_or_404(cover_id, auth)
    if format not in FORMATS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"bad_format: {format}")
    cdir = _covers_dir() / "covers" / cover_id
    cdir.mkdir(parents=True, exist_ok=True)
    dest = cdir / f"{format}.png"
    max_bytes = MAX_RENDER_MB * 1024 * 1024
    first = await image.read(_CHUNK)
    if first[:8] != b"\x89PNG\r\n\x1a\n":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="not_a_png")
    size = 0
    try:
        with open(dest, "wb") as out:
            chunk = first
            while chunk:
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"render_too_large: max {MAX_RENDER_MB}MB",
                    )
                out.write(chunk)
                chunk = await image.read(_CHUNK)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="write_failed")
    _get_store().set_render(cover_id, format, dest.name)
    log.info("cover_rendered", cover_id=cover_id, format=format, size=size)
    return {"ok": True, "format": format,
            "image_url": f"/api/orchestrator/covers/{cover_id}/image?format={format}"}


@router.get("/{cover_id}/image")
async def get_cover_image(
    cover_id: str, format: str = Query(default="9x16"),
    auth: AuthContext = Depends(require_auth),  # noqa: B008
):
    rec = _cover_owned_or_404(cover_id, auth)
    fn = (rec.get("renders") or {}).get(format)
    if not fn:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="render_not_ready")
    path = _covers_dir() / "covers" / cover_id / fn
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="render_missing")
    return FileResponse(path, media_type="image/png",
                        headers={"X-Content-Type-Options": "nosniff"})


@router.delete("/{cover_id}")
async def delete_cover(cover_id: str, auth: AuthContext = Depends(require_auth)):  # noqa: B008
    _cover_owned_or_404(cover_id, auth)
    import re
    if re.match(r"^[a-f0-9]{32}$", cover_id):
        shutil.rmtree(_covers_dir() / "covers" / cover_id, ignore_errors=True)
    ok = _get_store().delete_cover(cover_id)
    return {"deleted": ok}


# ── helpers ───────────────────────────────────────────────────────────────────
def _photo_owned_or_404(photo_id: str, auth: AuthContext) -> dict[str, Any]:
    rec = _get_store().get_photo(photo_id)
    if rec is None or (auth.enforce and rec.get("account_id") != auth.account_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="photo_not_found")
    return rec


def _cover_owned_or_404(cover_id: str, auth: AuthContext) -> dict[str, Any]:
    rec = _get_store().get_cover(cover_id)
    if rec is None or (auth.enforce and rec.get("account_id") != auth.account_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="cover_not_found")
    return rec


def _photo_public(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": p["id"], "url": f"/api/orchestrator/covers/photos/{p['id']}",
        "width": p.get("width"), "height": p.get("height"),
        "size_bytes": p.get("size_bytes"), "created_at": p.get("created_at"),
    }


def _cover_public(c: dict[str, Any]) -> dict[str, Any]:
    cid = c["id"]
    renders = c.get("renders") or {}
    return {
        "id": cid, "title": c.get("title") or "", "run_id": c.get("run_id"),
        "reel_id": c.get("reel_id"), "format": c.get("format"), "spec": c.get("spec") or {},
        "renders": {f: f"/api/orchestrator/covers/{cid}/image?format={f}" for f in renders},
        "created_at": c.get("created_at"), "updated_at": c.get("updated_at"),
    }
