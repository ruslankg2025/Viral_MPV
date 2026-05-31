"""Admin-эндпоинты publisher (prefix /publisher/admin).

Не проксируются shell-ом (blocked_first_segments={"admin"}).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from auth import require_admin_token
from config import get_settings
from state import state

router = APIRouter(
    prefix="/publisher/admin",
    tags=["publisher-admin"],
    dependencies=[Depends(require_admin_token)],
)


@router.get("/config")
async def get_config():
    """Текущая конфигурация (без секретов) — для диагностики."""
    s = get_settings()
    return {
        "default_dry_run": s.default_dry_run,
        "vk_api_version": s.vk_api_version,
        "vk_group_id": s.vk_group_id or None,
        "vk_token_set": bool(s.vk_access_token),
        "db_path": str(s.db_path),
        "antispam": {
            "rate_window_hours": s.antispam_rate_window_hours,
            "rate_limit": s.antispam_rate_limit,
            "content_cooldown_hours": s.antispam_content_cooldown_hours,
            "schedule_spread_step_min": s.schedule_spread_step_min,
            "schedule_spread_jitter_min": s.schedule_spread_jitter_min,
        },
    }


@router.get("/stats")
async def get_stats():
    """Счётчики публикаций по статусам."""
    store = state.publication_store
    counts: dict[str, int] = {}
    for row in store.query(limit=1000):
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"counts": counts}
