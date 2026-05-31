"""Pinterest-адаптер метрик.

Источник — Pinterest API v5, Pin analytics:
  GET https://api.pinterest.com/v5/pins/{pin_id}/analytics
  (https://developers.pinterest.com/docs/api/v5/pins-analytics)
Авторизация: OAuth Bearer access token (scope pins:read, user_accounts:read).

Запрашиваем metric_types и нормализуем (по доступным метрикам Pin analytics):
  - IMPRESSION    → reach        (показы пина; ближайшее к «охвату»)
  - SAVE          → saves        (сохранения)
  - PIN_CLICK     → views        (клики по пину = просмотр контента/детали)
  - OUTBOUND_CLICK→ click_through_to_external (переходы по ссылке наружу)
Doc по metric_types: https://developers.pinterest.com/docs/api/v5/pins-analytics
likes/comments у Pinterest как таковых нет (есть reactions, но в Pin analytics
основной набор — impression/save/click), поэтому likes=comments=shares=0.

Ответ analytics имеет форму:
  {"all": {"summary_metrics": {"IMPRESSION": N, "SAVE": M, ...}}, ...}
(агрегаты по pin_format/all; берём блок "all").

external_id публикации publisher для pinterest — это pin_id.

Без PINTEREST_TOKEN (token=None) — PinterestError; воркер берёт mock-метрики.
"""
from __future__ import annotations

from datetime import date, timedelta

import httpx

from platforms.base import PlatformAdapter, PlatformMetrics


PINTEREST_BASE_URL = "https://api.pinterest.com/v5"

# Метрики Pin analytics, которые запрашиваем.
PIN_METRIC_TYPES = ["IMPRESSION", "SAVE", "PIN_CLICK", "OUTBOUND_CLICK"]


def mock_metrics(external_id: str) -> dict[str, int]:
    """Детерминированные mock-метрики Pinterest (без токена / dev/тестов)."""
    seed = sum(ord(ch) for ch in external_id)
    impressions = 1500 + seed * 12
    return {
        "views": 200 + seed * 3,    # PIN_CLICK
        "reach": impressions,       # IMPRESSION
        "likes": 0,                 # у Pinterest нет «лайков» в Pin analytics
        "comments": 0,
        "shares": 0,
        "saves": 40 + seed % 17,    # SAVE
        "click_through_to_external": 10 + seed % 13,  # OUTBOUND_CLICK
    }


class PinterestError(RuntimeError):
    pass


class PinterestAdapter(PlatformAdapter):
    """Реальный адаптер Pinterest (API v5 Pin analytics).

    Доступно: impressions→reach, saves, pin_click→views,
    outbound_click→click_through_to_external. likes/comments/shares=0
    (нет в Pin analytics summary).
    """

    platform_name = "pinterest"

    def __init__(self, *, token: str | None = None, timeout: float = 15.0,
                 lookback_days: int = 30):
        super().__init__(token=token, timeout=timeout)
        # analytics требует диапазон дат (max 90 дней); берём последние N.
        self.lookback_days = lookback_days

    async def fetch_metrics(self, external_id: str) -> PlatformMetrics:
        if not self.token:
            raise PinterestError("pinterest_token_not_set")
        if not external_id:
            raise PinterestError("empty_external_id")

        end = date.today()
        start = end - timedelta(days=self.lookback_days)
        params = {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            # API v5 принимает повторяющийся query-param metric_types.
            "metric_types": PIN_METRIC_TYPES,
        }
        url = f"{PINTEREST_BASE_URL}/pins/{external_id}/analytics"
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params, headers=headers)
        except httpx.RequestError as e:
            raise PinterestError(f"network: {e}") from e
        if resp.status_code != 200:
            raise PinterestError(f"pinterest_http_{resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise PinterestError(f"pinterest_parse: {e}") from e

        # Ответ с ошибкой v5: {"code": N, "message": "..."}.
        if isinstance(data, dict) and "message" in data and "all" not in data:
            raise PinterestError(
                f"pinterest_api_error_{data.get('code')}: {data.get('message')}"
            )

        return self._parse(external_id, data)

    def _parse(self, external_id: str, data: dict) -> PlatformMetrics:
        """Достаёт summary_metrics из блока 'all' (или верхнего уровня)."""
        block = data.get("all") if isinstance(data, dict) else None
        summary = {}
        if isinstance(block, dict):
            summary = block.get("summary_metrics") or {}
        if not summary and isinstance(data, dict):
            # некоторые ответы кладут summary_metrics на верхний уровень
            summary = data.get("summary_metrics") or {}

        def _m(name: str) -> int:
            try:
                return int(summary.get(name) or 0)
            except (TypeError, ValueError):
                return 0

        return PlatformMetrics(
            platform=self.platform_name,
            external_id=external_id,
            views=_m("PIN_CLICK"),
            reach=_m("IMPRESSION"),
            likes=0,
            comments=0,
            shares=0,
            saves=_m("SAVE"),
            click_through_to_external=_m("OUTBOUND_CLICK"),
        )
