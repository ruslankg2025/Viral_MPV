"""FastAPI router для метрик из ccpm.poll_responses.

Endpoint:
    GET /api/insights/blog-daily?days=30&respondent=Алина

Если CCPM_DATABASE_URL не задан или БД недоступна — возвращает 503 с
понятным сообщением, фронт показывает empty-state и не падает.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from insights.client import InsightsError
from orchestrator.logging_setup import get_logger
from orchestrator.state import state

log = get_logger("insights.router")

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("/blog-daily")
async def get_blog_daily(
    days: int = Query(default=30, ge=1, le=365),
    respondent: str | None = Query(default=None),
):
    if state.insights_client is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="insights_not_configured",
        )
    try:
        rows = await state.insights_client.list_blog_daily(
            days=days, respondent=respondent
        )
    except InsightsError as e:
        log.warning("insights_query_failed", error=str(e))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        )
    return [
        {
            "date": r.response_date,
            "views": r.views,
            "reach": r.reach,
            "subs_growth": r.subs_growth,
            "interactions": r.interactions,
            "subs_pct": r.subs_pct,
            "non_subs_pct": r.non_subs_pct,
            "reels_pct": r.reels_pct,
            "posts_pct": r.posts_pct,
            "stories_pct": r.stories_pct,
            "top_reel": r.top_reel,
            "period": r.period,
        }
        for r in rows
    ]
