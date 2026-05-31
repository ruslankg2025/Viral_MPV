"""HTTP API сервиса publisher (prefix /publisher)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import require_worker_token
from config import get_settings
from logging_setup import get_logger
from schemas import (
    PublicationOut,
    PublishReq,
    PublishResp,
    RetryResp,
    ScheduleReq,
)
from service import execute_publication, resolve_dry_run
from state import state

log = get_logger()

router = APIRouter(
    prefix="/publisher",
    tags=["publisher"],
    dependencies=[Depends(require_worker_token)],
)


def _store():
    return state.publication_store


def _to_out(pub: dict[str, Any]) -> PublicationOut:
    return PublicationOut(**pub)


# ── список ────────────────────────────────────────────────────────────────────
@router.get("/publications", response_model=list[PublicationOut])
async def list_publications(
    limit: int = Query(default=200, ge=1, le=1000),
    platform: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    rows = _store().query(platform=platform, status=status, limit=limit)
    return [_to_out(r) for r in rows]


# ── немедленная публикация (dry-run или live) ─────────────────────────────────
@router.post("/publish", response_model=PublishResp)
async def publish(req: PublishReq):
    settings = get_settings()
    dry = resolve_dry_run(req.dry_run, settings)
    ids: list[str] = []
    for platform in req.platforms:
        pub = _store().create(
            platform=platform,
            title=req.title,
            description=req.description,
            tags=req.tags,
            video_path=req.video_path,
            status="publishing" if not dry else "dry_run",
        )
        updated = await execute_publication(
            pub, store=_store(), settings=settings, dry_run=dry
        )
        ids.append(updated["id"])
    overall = "dry_run" if dry else "done"
    log.info("publish_request", publication_ids=ids, dry_run=dry, platforms=req.platforms)
    return PublishResp(publication_ids=ids, status=overall)


# ── отложенная публикация (кладём в очередь scheduler-а) ──────────────────────
@router.post("/schedule", response_model=PublishResp)
async def schedule(req: ScheduleReq):
    ids: list[str] = []
    for platform in req.platforms:
        pub = _store().create(
            platform=platform,
            title=req.title,
            description=req.description,
            tags=req.tags,
            video_path=req.video_path,
            status="scheduled",
            scheduled_at=req.scheduled_at,
        )
        ids.append(pub["id"])
    log.info("schedule_request", publication_ids=ids, scheduled_at=req.scheduled_at,
             platforms=req.platforms)
    return PublishResp(publication_ids=ids, status="scheduled")


# ── повтор упавшей публикации ─────────────────────────────────────────────────
@router.post("/retry/{publication_id}", response_model=RetryResp)
async def retry(publication_id: str):
    pub = _store().get(publication_id)
    if pub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="publication_not_found")
    settings = get_settings()
    # Повторяем тем же режимом, что и DEFAULT_DRY_RUN (явный флаг в записи не храним).
    dry = settings.default_dry_run
    updated = await execute_publication(pub, store=_store(), settings=settings, dry_run=dry)
    return RetryResp(status=updated["status"])


# ── одиночная запись ──────────────────────────────────────────────────────────
@router.get("/publications/{publication_id}", response_model=PublicationOut)
async def get_publication(publication_id: str):
    pub = _store().get(publication_id)
    if pub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="publication_not_found")
    return _to_out(pub)


@router.delete("/publications/{publication_id}", status_code=204)
async def delete_publication(publication_id: str):
    if not _store().delete(publication_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="publication_not_found")
    return None
