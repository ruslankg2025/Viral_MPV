"""FastAPI router для метрик от ментор-бота.

Эндпоинты:
- POST /api/insights/blog-daily — write (auth required, X-Worker-Token)
- GET  /api/insights/blog-daily?days=30 — read (без auth)
- GET  /api/insights/health — статистика свежести данных (без auth)

GET-эндпоинты всегда работают: если БД пустая, отдают [] и
{total_rows: 0}. POST требует валидный INSIGHTS_WRITE_TOKEN в env.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from insights.auth import require_insights_write_token
from insights.schemas import BlogDailyPayload
from orchestrator.logging_setup import get_logger
from orchestrator.state import state

log = get_logger("insights.router")

router = APIRouter(prefix="/api/insights", tags=["insights"])


def _ensure_store():
    if state.insights_store is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="insights_store_not_initialized",
        )
    return state.insights_store


@router.post("/blog-daily", dependencies=[Depends(require_insights_write_token)])
async def post_blog_daily(payload: BlogDailyPayload):
    store = _ensure_store()
    rid, created = store.upsert_blog_daily(
        respondent=payload.respondent,
        account_id=payload.account_id,
        response_date=payload.response_date.isoformat(),
        responded_at=payload.responded_at.isoformat() if payload.responded_at else None,
        data=payload.data.model_dump(exclude_none=False),
    )
    log.info(
        "insights_blog_daily_upsert",
        id=rid,
        created=created,
        respondent=payload.respondent,
        account_id=payload.account_id,
        response_date=payload.response_date.isoformat(),
    )
    return {"id": rid, "created": created}


@router.get("/blog-daily")
async def get_blog_daily(
    days: int = Query(default=30, ge=1, le=365),
    respondent: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
):
    store = _ensure_store()
    rows = store.list_blog_daily(
        days=days, respondent=respondent, account_id=account_id
    )
    # Фронт ожидает плоский формат с метриками на верхнем уровне (как
    # старый GET от ccpm-клиента). Раскрываем data в верхний уровень,
    # сохраняя date/respondent/account_id.
    out = []
    for r in rows:
        d = r.get("data") or {}
        out.append({
            "date": r["response_date"],
            "respondent": r["respondent"],
            "account_id": r["account_id"],
            "responded_at": r["responded_at"],
            "views": d.get("views"),
            "reach": d.get("reach"),
            "subs_growth": d.get("subs_growth"),
            "interactions": d.get("interactions"),
            "subs_pct": d.get("subs_pct"),
            "non_subs_pct": d.get("non_subs_pct"),
            "reels_pct": d.get("reels_pct"),
            "posts_pct": d.get("posts_pct"),
            "stories_pct": d.get("stories_pct"),
            "top_reel": d.get("top_reel"),
            "period": d.get("period"),
            "reach_growth_pct": d.get("reach_growth_pct"),
            "content_shared": d.get("content_shared"),
        })
    return out


@router.get("/health")
async def get_health():
    store = _ensure_store()
    return store.get_health()
