"""Admin CRUD-эндпоинты для API-ключей script.

DUP: копия Modules/processor/api/admin_keys.py с префиксом /script/admin.
Переиспользует те же pricing/keys из viral_llm.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from auth import require_admin_token
from state import state
from viral_llm.keys.pricing import ALL_PROVIDERS, PRICING

router = APIRouter(
    prefix="/script/admin",
    tags=["script-admin"],
    dependencies=[Depends(require_admin_token)],
)


class CreateKeyReq(BaseModel):
    provider: str
    label: str | None = None
    secret: str
    priority: int = 100
    monthly_limit_usd: float | None = None
    is_active: bool = True


class UpdateKeyReq(BaseModel):
    label: str | None = None
    priority: int | None = None
    is_active: bool | None = None
    monthly_limit_usd: float | None = None
    secret: str | None = None


def _enrich(key: dict[str, Any]) -> dict[str, Any]:
    key = dict(key)
    key["usage_30d"] = state.key_store.usage_30d_summary(key["id"])
    key["month_cost_usd"] = round(state.key_store.month_cost(key["id"]), 6)
    return key


@router.get("/providers")
async def list_providers():
    return {p: PRICING[p] for p in ALL_PROVIDERS}


@router.get("/api-keys")
async def list_keys():
    return [_enrich(k) for k in state.key_store.list_all()]


@router.get("/api-keys/{key_id}")
async def get_key(key_id: int):
    k = state.key_store.get(key_id)
    if not k:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="key_not_found")
    return _enrich(k)


@router.post("/api-keys", status_code=201)
async def create_key(req: CreateKeyReq):
    if req.provider not in ALL_PROVIDERS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"unknown_provider:{req.provider}",
        )
    try:
        k = state.key_store.create(
            provider=req.provider,
            label=req.label,
            secret=req.secret,
            priority=req.priority,
            monthly_limit_usd=req.monthly_limit_usd,
            is_active=req.is_active,
        )
    except Exception as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"create_failed:{e}"
        ) from e
    return _enrich(k)


@router.patch("/api-keys/{key_id}")
async def update_key(key_id: int, req: UpdateKeyReq):
    k = state.key_store.get(key_id)
    if not k:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="key_not_found")
    updated = state.key_store.update(
        key_id,
        label=req.label,
        priority=req.priority,
        is_active=req.is_active,
        monthly_limit_usd=req.monthly_limit_usd,
        secret=req.secret,
    )
    return _enrich(updated)


@router.delete("/api-keys/{key_id}", status_code=204)
async def delete_key(key_id: int):
    if not state.key_store.delete(key_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="key_not_found")
    return None


@router.get("/usage")
async def get_usage(
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
):
    return state.key_store.usage_aggregate(since=since, until=until)
