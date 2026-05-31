"""Тесты ZenAdapter — заглушка (публичного API метрик у Дзена нет).

Адаптер НЕ делает HTTP и всегда возвращает детерминированные mock-метрики
по external_id, без необходимости в токене.
"""
import pytest

from platforms.zen import ZenAdapter, ZenError, mock_metrics


@pytest.mark.asyncio
async def test_fetch_metrics_returns_deterministic_mock_without_token():
    adapter = ZenAdapter()  # без токена — Дзену он не нужен
    m1 = await adapter.fetch_metrics("zen_123")
    m2 = await adapter.fetch_metrics("zen_123")
    assert m1.platform == "zen"
    assert m1.external_id == "zen_123"
    assert m1.views > 0
    # детерминированно по external_id
    assert (m1.views, m1.reach, m1.likes) == (m2.views, m2.reach, m2.likes)
    expected = mock_metrics("zen_123")
    assert m1.views == expected["views"]
    assert m1.reach == expected["reach"]


@pytest.mark.asyncio
async def test_fetch_metrics_empty_external_id_raises():
    adapter = ZenAdapter()
    with pytest.raises(ZenError):
        await adapter.fetch_metrics("")


def test_mock_metrics_deterministic_and_saves_zero():
    a = mock_metrics("zen_1")
    assert a == mock_metrics("zen_1")
    assert a["saves"] == 0  # у Дзена нет «сохранений»
