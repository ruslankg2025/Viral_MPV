"""Admin CRUD-эндпоинты для Template Registry.

DUP: аналог Modules/processor/prompts/router.py, с префиксом /script/admin/templates.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import require_admin_token
from state import state

router = APIRouter(
    prefix="/script/admin/templates",
    tags=["script-admin"],
    dependencies=[Depends(require_admin_token)],
)


class CreateTemplateReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    version: str = Field(..., min_length=1, max_length=50)
    body: str = Field(..., min_length=1)
    metadata: dict[str, Any] | None = None
    is_active: bool = False


def _store():
    s = getattr(state, "template_store", None)
    if s is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "template_store_unavailable")
    return s


@router.get("")
async def list_templates() -> list[dict[str, Any]]:
    return _store().list_all()


@router.get("/{name}")
async def list_versions(name: str) -> list[dict[str, Any]]:
    versions = _store().list_versions(name)
    if not versions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template_name_not_found")
    return versions


@router.get("/{name}/{version}")
async def get_template_version(name: str, version: str) -> dict[str, Any]:
    rec = _store().get_raw(name, version)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template_version_not_found")
    return rec


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_template(req: CreateTemplateReq) -> dict[str, Any]:
    try:
        return _store().create(
            name=req.name,
            version=req.version,
            body=req.body,
            metadata=req.metadata,
            is_active=req.is_active,
        )
    except Exception as e:
        raise HTTPException(status.HTTP_409_CONFLICT, f"create_failed: {e}")


@router.patch("/{name}/activate/{version}")
async def activate_version(name: str, version: str) -> dict[str, Any]:
    rec = _store().activate(name, version)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template_version_not_found")
    return rec


@router.delete("/{name}/{version}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_version(name: str, version: str) -> None:
    try:
        ok = _store().delete(name, version)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template_version_not_found")
