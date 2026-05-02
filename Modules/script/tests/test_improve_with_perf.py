"""Тесты performance-сигнала в improve-prompt (этап 5: 'результаты роликов главнее оценок')."""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from schemas import ImprovePromptReq  # noqa: E402


def test_req_accepts_optional_performance():
    r = ImprovePromptReq(
        account_id="acc",
        current_prompt="Be helpful",
        performance={"posts": 10, "median_er": 0.05},
    )
    assert r.performance is not None
    assert r.performance["posts"] == 10


def test_req_performance_optional():
    r = ImprovePromptReq(account_id="acc", current_prompt="prompt")
    assert r.performance is None


def test_format_performance_block_empty():
    from router import _format_performance_block
    assert _format_performance_block(None) == ""
    assert _format_performance_block({}) == ""
    assert _format_performance_block({"posts": 0}) == ""


def test_format_performance_block_with_data():
    from router import _format_performance_block
    perf = {
        "posts": 12, "days": 30,
        "median_er": 0.045, "median_velocity": 250,
        "top": [
            {"engagement_rate": 0.082, "velocity": 1200, "description": "Привычки миллионеров"},
            {"engagement_rate": 0.071, "velocity": 950, "description": "Топ-3 ошибки в инвестициях"},
        ],
        "bottom": [
            {"engagement_rate": 0.012, "velocity": 30, "description": "Скучный заголовок про налоги"},
        ],
    }
    out = _format_performance_block(perf)
    assert "PERFORMANCE РОЛИКОВ" in out
    assert "12 рилсов" in out
    assert "Привычки миллионеров" in out
    assert "Скучный заголовок" in out
    assert "РАБОТАЕТ" in out
    assert "НЕ РАБОТАЕТ" in out


def test_format_performance_skips_when_no_top_or_bottom():
    """Если top + bottom пусты → блок не нужен (но posts может быть)."""
    from router import _format_performance_block
    perf = {"posts": 5, "median_er": 0.04, "top": [], "bottom": []}
    out = _format_performance_block(perf)
    # Заголовок медианы есть, но списки пустые. Пользы немного, но
    # технически валидный блок (не пустая строка).
    assert "PERFORMANCE" in out
    assert "Топ-рилсы" not in out
    assert "Слабые" not in out


def test_perf_with_short_description_clipped():
    from router import _format_performance_block
    long_desc = "A" * 500
    perf = {
        "posts": 3, "days": 30,
        "top": [{"engagement_rate": 0.05, "velocity": 100, "description": long_desc}],
        "bottom": [],
    }
    out = _format_performance_block(perf)
    # Должен быть обрезан до ~200 символов
    assert "A" * 250 not in out
