"""Автомонтаж (вкладка «Монтаж») — shell-фасад к сервису montage.

Почему не общий `_proxy`: аплоад сырья (сотни МБ) и скачивание 4К-результата
нельзя гонять через полнобуферный прокси (RAM shell). Поэтому:
  • аплоад — стримим на общий том montage_data/incoming, затем зовём montage
    `/jobs/from-path` (он переносит файл в каталог джоба);
  • результат — стримим из montage через httpx `stream=True` → StreamingResponse.

Владелец берётся ТОЛЬКО из сессии (require_auth) и прокидывается в montage
заголовком X-Account-Id (montage фильтрует джобы по нему; чужой → 404).
"""
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from auth.deps import AuthContext, require_auth
from orchestrator.logging_setup import get_logger

log = get_logger("montage")

router = APIRouter(prefix="/api/montage", tags=["montage"])

MONTAGE_URL = os.getenv("MONTAGE_URL", "http://montage:8000").rstrip("/")
MONTAGE_TOKEN = os.getenv("MONTAGE_TOKEN", "dev-worker-token-change-me")
# Общий том montage_data (в montage смонтирован как WORK_DIR=/montage).
MONTAGE_DIR = Path(os.getenv("MONTAGE_DIR", "/montage"))
# Держим ≤ nginx client_max_body_size (сейчас 320M на проде). Для большего
# сырья (мультикам) поднять nginx И MONTAGE_MAX_MB.
MAX_VIDEO_MB = int(os.getenv("MONTAGE_MAX_MB", "300"))

_CHUNK = 1024 * 1024
_ALLOWED_VIDEO_CT = {"video/mp4", "video/quicktime", "video/webm", "application/octet-stream"}
_EXT_BY_CT = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm"}


def _headers(auth: AuthContext) -> dict[str, str]:
    h = {"X-Worker-Token": MONTAGE_TOKEN}
    if auth.enforce and auth.account_id:
        h["X-Account-Id"] = auth.account_id
    return h


def _incoming_dir() -> Path:
    d = MONTAGE_DIR / "incoming"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.post("/jobs", status_code=status.HTTP_201_CREATED)
async def create_job(
    source: UploadFile = File(...),
    title: str | None = Form(default=None),
    model: str | None = Form(default=None),
    width: int | None = Form(default=None),
    height: int | None = Form(default=None),
    language: str | None = Form(default=None),
    force: bool = Form(default=False),
    auth: AuthContext = Depends(require_auth),  # noqa: B008
):
    ct = (source.content_type or "").lower()
    if ct and ct not in _ALLOWED_VIDEO_CT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"unsupported_type: {ct}")

    ext = _EXT_BY_CT.get(ct, Path(source.filename or "").suffix.lower() or ".mp4")
    staged = _incoming_dir() / f"{uuid.uuid4().hex}{ext}"

    # 1) стрим сырья на общий том (не в память shell), с лимитом размера
    size = 0
    max_bytes = MAX_VIDEO_MB * 1024 * 1024
    try:
        with open(staged, "wb") as out:
            while chunk := await source.read(_CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"file_too_large: max {MAX_VIDEO_MB}MB",
                    )
                out.write(chunk)
    except HTTPException:
        staged.unlink(missing_ok=True)
        raise
    except Exception as e:  # noqa: BLE001
        staged.unlink(missing_ok=True)
        log.warning("montage_stage_failed", error=str(e))
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="stage_failed")

    # 2) создаём джоб в montage по пути (он перенесёт файл в каталог джоба)
    payload: dict[str, Any] = {
        "source_path": str(staged),
        "source_filename": source.filename,
        "title": title,
        "model": model,
        "width": width,
        "height": height,
        "language": language,
        "force": bool(force),
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as c:
            r = await c.post(
                f"{MONTAGE_URL}/montage/jobs/from-path",
                headers={**_headers(auth), "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.RequestError as e:
        staged.unlink(missing_ok=True)
        raise HTTPException(502, detail=f"montage_unreachable: {type(e).__name__}")
    if r.status_code >= 400:
        staged.unlink(missing_ok=True)  # montage не забрал файл — не оставляем мусор
    log.info("montage_job_created", account_id=auth.account_id, size=size, status=r.status_code)
    return JSONResponse(content=_safe_json(r), status_code=r.status_code)


@router.get("/jobs")
async def list_jobs(
    status_filter: str | None = Query(default=None, alias="status"),
    auth: AuthContext = Depends(require_auth),  # noqa: B008
):
    params = {"status": status_filter} if status_filter else None
    return await _get_json("/montage/jobs", auth, params=params)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, auth: AuthContext = Depends(require_auth)):  # noqa: B008
    return await _get_json(f"/montage/jobs/{job_id}", auth)


@router.get("/jobs/{job_id}/log")
async def get_job_log(job_id: str, auth: AuthContext = Depends(require_auth)):  # noqa: B008
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(f"{MONTAGE_URL}/montage/jobs/{job_id}/log", headers=_headers(auth))
    except httpx.RequestError as e:
        raise HTTPException(502, detail=f"montage_unreachable: {type(e).__name__}")
    return PlainTextResponse(r.text, status_code=r.status_code)


@router.get("/jobs/{job_id}/result")
async def get_job_result(job_id: str, auth: AuthContext = Depends(require_auth)):  # noqa: B008
    """Стримим 4К-мастер из montage (ownership проверяет montage) — не буферим."""
    client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=15.0))
    url = f"{MONTAGE_URL}/montage/jobs/{job_id}/result"
    try:
        req = client.build_request("GET", url, headers=_headers(auth))
        resp = await client.send(req, stream=True)
    except httpx.RequestError as e:
        await client.aclose()
        raise HTTPException(502, detail=f"montage_unreachable: {type(e).__name__}")
    if resp.status_code != 200:
        body = await resp.aread()
        await resp.aclose()
        await client.aclose()
        return JSONResponse(content=_loads(body), status_code=resp.status_code)

    async def _stream():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        _stream(), media_type="video/mp4",
        headers={"Content-Disposition": f'attachment; filename="{job_id}.mp4"'},
    )


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str, auth: AuthContext = Depends(require_auth)):  # noqa: B008
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.delete(f"{MONTAGE_URL}/montage/jobs/{job_id}", headers=_headers(auth))
    except httpx.RequestError as e:
        raise HTTPException(502, detail=f"montage_unreachable: {type(e).__name__}")
    return JSONResponse(content=_safe_json(r), status_code=r.status_code)


# ── helpers ────────────────────────────────────────────────────────────────────
async def _get_json(path: str, auth: AuthContext, params: dict | None = None):
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(f"{MONTAGE_URL}{path}", headers=_headers(auth), params=params)
    except httpx.RequestError as e:
        raise HTTPException(502, detail=f"montage_unreachable: {type(e).__name__}")
    return JSONResponse(content=_safe_json(r), status_code=r.status_code)


def _safe_json(r: httpx.Response) -> Any:
    return _loads(r.content)


def _loads(b: bytes) -> Any:
    import json
    try:
        return json.loads(b)
    except Exception:  # noqa: BLE001
        return {"detail": (b[:200].decode("utf-8", "replace") if b else "")}
