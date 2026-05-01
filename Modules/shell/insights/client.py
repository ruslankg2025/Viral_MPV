"""Async-клиент к ccpm.poll_responses (Postgres) для метрик от Алины.

Шелл-сервис не пишет в эту БД — только read-only. Токен/credentials
живут в .env.shell (CCPM_DATABASE_URL).

Структура таблицы (упрощённо):
    id              SERIAL PK
    template_code   TEXT       -- 'blog_daily' | 'blog_content_daily' | ...
    respondent      TEXT       -- 'Алина' и т.п.
    response_date   DATE       -- день за который данные
    responded_at    TIMESTAMPTZ
    data            JSONB      -- метрики

Метрики blog_daily (известные ключи):
    views, reach, subs_growth, interactions
    subs_pct, non_subs_pct           -- источник охвата (донат)
    reels_pct, posts_pct, stories_pct -- тип контента
    reach_growth_pct, content_shared, top_reel, period
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg


class InsightsError(RuntimeError):
    pass


@dataclass
class BlogDailyRow:
    """Одна строка blog_daily (latest по response_date)."""
    response_date: str  # ISO-date
    views: int | None
    reach: int | None
    subs_growth: int | None
    interactions: int | None
    subs_pct: float | None
    non_subs_pct: float | None
    reels_pct: float | None
    posts_pct: float | None
    stories_pct: float | None
    top_reel: str | None
    period: str | None


class InsightsClient:
    """Минимальный async-клиент с lazy-pool. Если URL пустой / провайдер
    недоступен — все методы кидают InsightsError; роутер ловит и отдаёт 503.
    """

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            try:
                self._pool = await asyncpg.create_pool(
                    self.dsn,
                    min_size=1,
                    max_size=3,
                    command_timeout=5.0,
                )
            except Exception as e:  # noqa: BLE001
                raise InsightsError(f"insights_db_unreachable: {e}") from e
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def list_blog_daily(
        self, *, days: int = 30, respondent: str | None = None
    ) -> list[BlogDailyRow]:
        """Latest record per response_date за последние N дней.

        Алина может прислать по нескольку записей в день — берём самую
        свежую (по responded_at). Сортируем результат по дате ASC для
        spark-чарта.
        """
        pool = await self._get_pool()
        params: list[Any] = [days]
        respondent_clause = ""
        if respondent:
            respondent_clause = " AND respondent = $2"
            params.append(respondent)
        sql = f"""
            SELECT DISTINCT ON (response_date)
                response_date,
                (data->>'views')::int           AS views,
                (data->>'reach')::int           AS reach,
                (data->>'subs_growth')::int     AS subs_growth,
                (data->>'interactions')::int    AS interactions,
                (data->>'subs_pct')::float      AS subs_pct,
                (data->>'non_subs_pct')::float  AS non_subs_pct,
                (data->>'reels_pct')::float     AS reels_pct,
                (data->>'posts_pct')::float     AS posts_pct,
                (data->>'stories_pct')::float   AS stories_pct,
                (data->>'top_reel')             AS top_reel,
                (data->>'period')               AS period
            FROM ccpm.poll_responses
            WHERE template_code = 'blog_daily'
              AND response_date >= CURRENT_DATE - ($1::int * INTERVAL '1 day')
              {respondent_clause}
            ORDER BY response_date DESC, responded_at DESC
        """
        try:
            rows = await pool.fetch(sql, *params)
        except Exception as e:  # noqa: BLE001
            raise InsightsError(f"insights_query_failed: {e}") from e

        # Возвращаем ASC (для chart-ов удобнее timeline слева→направо)
        items = [
            BlogDailyRow(
                response_date=r["response_date"].isoformat(),
                views=r["views"],
                reach=r["reach"],
                subs_growth=r["subs_growth"],
                interactions=r["interactions"],
                subs_pct=r["subs_pct"],
                non_subs_pct=r["non_subs_pct"],
                reels_pct=r["reels_pct"],
                posts_pct=r["posts_pct"],
                stories_pct=r["stories_pct"],
                top_reel=r["top_reel"],
                period=r["period"],
            )
            for r in rows
        ]
        items.sort(key=lambda x: x.response_date)
        return items
