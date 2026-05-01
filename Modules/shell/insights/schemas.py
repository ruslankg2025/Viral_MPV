"""Pydantic-модели для insights endpoint-ов.

BlogDailyData принимает 13 известных полей метрик + extra='allow' для
forward-совместимости (если бот в будущем добавит новые ключи —
пройдут через и попадут в data_json без правок VIRA).
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class BlogDailyData(BaseModel):
    views: int | None = None
    reach: int | None = None
    subs_growth: int | None = None
    interactions: int | None = None
    subs_pct: float | None = None
    non_subs_pct: float | None = None
    reels_pct: float | None = None
    posts_pct: float | None = None
    stories_pct: float | None = None
    top_reel: str | None = None
    period: str | None = None
    reach_growth_pct: float | None = None
    content_shared: int | None = None

    model_config = {"extra": "allow"}


class BlogDailyPayload(BaseModel):
    respondent: str
    account_id: str | None = None
    response_date: date          # принимает 'YYYY-MM-DD'
    responded_at: datetime | None = None
    data: BlogDailyData
