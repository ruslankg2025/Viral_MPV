"""
Watchlist selector + evaluator.

Задача — каждое утро отобрать top-N рилсов per source в «мониторинг»
и поддерживать их жизненный цикл:

- select_daily_topn: для каждого активного source берём top-N по velocity
  из свежих (≤ freshness_hours) видео с минимальным возрастом (min_age_hours
  анти-шум) и добавляем в watchlist.
- evaluate_watchlist: для активных записей, у которых истёк TTL (expires_at < now),
  проставляем финальный статус hit | miss | stalled. Graduation может сработать
  и до истечения — если ролик явно «взлетел», помечаем его как hit заранее.

Правила graduate/expire:
  views_now >= initial_views * (1 + delta_pct)  OR  velocity_now >= velocity_hi
      → hit (+ graduated_at, hit_reason='delta_pct' | 'velocity_hi')
  при истечении TTL:
      views_now < initial_views * 1.2  → miss
      иначе                             → stalled
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from storage import MonitorStore, WatchlistRow


@dataclass
class SelectionResult:
    added: int
    graduated: int
    expired: int
    candidates_seen: int


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def select_daily_topn(
    store: MonitorStore,
    *,
    top_n: int = 5,
    ttl_days: int = 3,
    freshness_hours: int = 48,
    min_age_hours: float = 2.0,
    velocity_hi: float = 5000.0,
    delta_pct: float = 2.0,
    source_id: str | None = None,
) -> SelectionResult:
    """Основная job: отобрать top-N per source, эвалюировать активные.

    source_id=None → обход всех активных источников (cron-mode).
    source_id задан → обход одного источника (вызывается после ручного crawl
    из UI, чтобы watchlist обновился немедленно без ожидания 08:00 UTC).

    Возвращает агрегированную статистику.
    """
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=freshness_hours)).isoformat()

    added_total = 0
    candidates_total = 0

    if source_id is not None:
        one = store.get_source(source_id)
        sources = [one] if (one is not None and one.is_active) else []
    else:
        sources = store.list_sources(active_only=True)

    for src in sources:
        cands = store.list_watchlist_candidates(
            src.id, since, min_age_hours=min_age_hours
        )
        candidates_total += len(cands)
        # cands уже отсортированы по velocity DESC
        picked = 0
        for video, trending, snap in cands:
            if picked >= top_n:
                break
            if store.is_watched(video.id):
                # уже под мониторингом — не считаем как picked, но и не добавляем
                continue
            row = store.add_to_watchlist(
                video_id=video.id,
                source_id=src.id,
                published_at=video.published_at,
                initial_views=snap.views if snap else 0,
                initial_velocity=trending.velocity if trending else None,
                ttl_days=ttl_days,
            )
            if row is not None:
                added_total += 1
                picked += 1

    graduated, expired = evaluate_watchlist(
        store,
        velocity_hi=velocity_hi,
        delta_pct=delta_pct,
        now=now,
    )
    return SelectionResult(
        added=added_total,
        graduated=graduated,
        expired=expired,
        candidates_seen=candidates_total,
    )


def evaluate_watchlist(
    store: MonitorStore,
    *,
    velocity_hi: float = 5000.0,
    delta_pct: float = 2.0,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Проходит по активным watchlist-записям:
      - если прошёл порог hit → mark graduated (независимо от TTL)
      - если TTL истёк и не hit → stalled/miss

    Возвращает (graduated_count, expired_count).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    graduated = 0
    expired = 0
    rows: list[WatchlistRow] = store.list_active_watchlist_all()

    for row in rows:
        snap = store.latest_snapshot(row.video_id)
        trending = store.latest_trending(row.video_id)
        views_now = snap.views if snap else row.initial_views
        velocity_now = trending.velocity if trending else None

        # --- 1) Проверка graduate (может сработать до TTL) ---
        hit_reason: str | None = None
        if (
            row.initial_views > 0
            and views_now >= row.initial_views * (1.0 + delta_pct)
        ):
            hit_reason = "delta_pct"
        elif velocity_now is not None and velocity_now >= velocity_hi:
            hit_reason = "velocity_hi"

        if hit_reason is not None:
            store.mark_watchlist_status(
                row.id,
                status="hit",
                hit_reason=hit_reason,
                graduated=True,
                now_iso=now_iso,
            )
            graduated += 1
            continue

        # --- 2) Проверка TTL ---
        expires_dt = _parse_iso(row.expires_at)
        if expires_dt is not None and expires_dt < now:
            if views_now < row.initial_views * 1.2:
                final = "miss"
            else:
                final = "stalled"
            store.mark_watchlist_status(
                row.id,
                status=final,
                now_iso=now_iso,
            )
            expired += 1

    return graduated, expired
