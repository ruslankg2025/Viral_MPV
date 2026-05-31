"""Агрегации метрик на pandas.

Все функции принимают «сырые» строки из AnalyticsStore и считают сводки.
Чтобы повторные fetch-и одной публикации не суммировались многократно,
для cross-platform/top/platform мы используем ПОСЛЕДНИЙ срез на публикацию
(`latest`-строки из store), а здесь — только агрегируем.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd


METRIC_COLUMNS = [
    "views", "reach", "likes", "comments",
    "shares", "saves", "click_through_to_external",
]


def period_to_since(period: str | None) -> str | None:
    """'7d'/'30d'/'1d'/'all' → ISO-таймстамп начала окна (или None для 'all')."""
    if not period or period == "all":
        return None
    period = period.strip().lower()
    try:
        if period.endswith("d"):
            days = int(period[:-1])
        elif period.endswith("h"):
            days = int(period[:-1]) / 24
        else:
            days = int(period)
    except ValueError:
        return None
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return since.isoformat()


def _df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=["publication_id", "platform", *METRIC_COLUMNS])
    for col in METRIC_COLUMNS:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype("int64")
    return df


def _engagement(row: pd.Series) -> int:
    return int(row["likes"] + row["comments"] + row["shares"] + row["saves"])


def cross_platform(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Сводка по платформам + общий тотал. rows — latest-срезы."""
    df = _df(rows)
    totals = {c: int(df[c].sum()) for c in METRIC_COLUMNS}
    engagement = int(df.apply(_engagement, axis=1).sum()) if not df.empty else 0
    total_views = totals["views"]
    er = round(engagement / total_views * 100, 2) if total_views else 0.0

    platforms = []
    if not df.empty:
        grouped = df.groupby("platform")
        for platform, g in grouped:
            p_views = int(g["views"].sum())
            p_eng = int(g.apply(_engagement, axis=1).sum())
            platforms.append({
                "platform": platform,
                "publications": int(g["publication_id"].nunique()),
                **{c: int(g[c].sum()) for c in METRIC_COLUMNS},
                "engagement": p_eng,
                "engagement_rate": round(p_eng / p_views * 100, 2) if p_views else 0.0,
            })
        platforms.sort(key=lambda p: p["views"], reverse=True)

    return {
        "totals": totals,
        "engagement": engagement,
        "engagement_rate": er,
        "publications": int(df["publication_id"].nunique()) if not df.empty else 0,
        "platforms": platforms,
    }


def platform_summary(rows: list[dict[str, Any]], platform: str) -> dict[str, Any]:
    """Сводка по одной платформе + список её публикаций. rows — latest-срезы."""
    df = _df(rows)
    df = df[df["platform"] == platform] if not df.empty else df
    totals = {c: int(df[c].sum()) for c in METRIC_COLUMNS}
    engagement = int(df.apply(_engagement, axis=1).sum()) if not df.empty else 0
    p_views = totals["views"]
    publications = []
    if not df.empty:
        for _, r in df.iterrows():
            publications.append({
                "publication_id": r["publication_id"],
                **{c: int(r[c]) for c in METRIC_COLUMNS},
                "engagement": _engagement(r),
            })
        publications.sort(key=lambda p: p["views"], reverse=True)
    return {
        "platform": platform,
        "totals": totals,
        "engagement": engagement,
        "engagement_rate": round(engagement / p_views * 100, 2) if p_views else 0.0,
        "publications_count": int(df["publication_id"].nunique()) if not df.empty else 0,
        "publications": publications,
    }


def publication_timeseries(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """История метрик одной публикации (все срезы во времени) + последнее."""
    df = _df(rows)
    if df.empty:
        return {"history": [], "latest": None}
    if "fetched_at" in df.columns:
        df = df.sort_values("fetched_at")
    history = []
    for _, r in df.iterrows():
        history.append({
            "fetched_at": r.get("fetched_at"),
            **{c: int(r[c]) for c in METRIC_COLUMNS},
            "engagement": _engagement(r),
        })
    return {"history": history, "latest": history[-1] if history else None}


def ab_compare(rows_by_id: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """A/B сравнение нескольких публикаций по последнему срезу каждой."""
    variants = []
    for pub_id, rows in rows_by_id.items():
        df = _df(rows)
        if df.empty:
            variants.append({"publication_id": pub_id, "found": False})
            continue
        if "fetched_at" in df.columns:
            df = df.sort_values("fetched_at")
        last = df.iloc[-1]
        v_views = int(last["views"])
        eng = _engagement(last)
        variants.append({
            "publication_id": pub_id,
            "found": True,
            "platform": last.get("platform"),
            **{c: int(last[c]) for c in METRIC_COLUMNS},
            "engagement": eng,
            "engagement_rate": round(eng / v_views * 100, 2) if v_views else 0.0,
        })
    found = [v for v in variants if v.get("found")]
    winner = max(found, key=lambda v: v["engagement_rate"], default=None)
    return {
        "variants": variants,
        "winner": winner["publication_id"] if winner else None,
        "winner_by": "engagement_rate",
    }


def top_publications(
    rows: list[dict[str, Any]], *, metric: str, limit: int,
) -> list[dict[str, Any]]:
    """Топ публикаций по метрике. rows — latest-срезы.

    metric ∈ METRIC_COLUMNS | 'engagement' | 'engagement_rate'.
    """
    df = _df(rows)
    if df.empty:
        return []
    df = df.copy()
    df["engagement"] = df.apply(_engagement, axis=1)
    df["engagement_rate"] = df.apply(
        lambda r: round(r["engagement"] / r["views"] * 100, 2) if r["views"] else 0.0,
        axis=1,
    )
    if metric not in (*METRIC_COLUMNS, "engagement", "engagement_rate"):
        metric = "views"
    df = df.sort_values(metric, ascending=False).head(limit)
    out = []
    for _, r in df.iterrows():
        out.append({
            "publication_id": r["publication_id"],
            "platform": r.get("platform"),
            **{c: int(r[c]) for c in METRIC_COLUMNS},
            "engagement": int(r["engagement"]),
            "engagement_rate": float(r["engagement_rate"]),
            "metric": metric,
            "value": float(r[metric]),
        })
    return out
