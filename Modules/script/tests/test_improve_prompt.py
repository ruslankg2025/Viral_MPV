"""Тесты валидации ImprovePromptReq + статус-логика без реального LLM
(этап 5 self-learning agent)."""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from schemas import ImprovePromptReq, ImprovePromptResp  # noqa: E402


def test_improve_req_validates_required_fields():
    r = ImprovePromptReq(account_id="acc", current_prompt="You are a writer")
    assert r.days == 14
    # min_events=3 после введения performance-сигнала (perf даёт основу
    # даже при минимуме feedback, см. _improve_one_account)
    assert r.min_events == 3
    assert r.performance is None


def test_improve_req_rejects_short_prompt():
    with pytest.raises(Exception):
        ImprovePromptReq(account_id="acc", current_prompt="")


def test_improve_req_clamps_days():
    with pytest.raises(Exception):
        ImprovePromptReq(account_id="acc", current_prompt="p", days=500)
    with pytest.raises(Exception):
        ImprovePromptReq(account_id="acc", current_prompt="p", days=0)


def test_improve_resp_status_options():
    """Принимаемые status-значения в Pydantic Literal."""
    for s in ("improved", "not_enough_data", "no_pattern"):
        r = ImprovePromptResp(
            status=s, feedback_count=0, loved_count=0, hated_count=0,
        )
        assert r.status == s
    with pytest.raises(Exception):
        ImprovePromptResp(
            status="explode", feedback_count=0, loved_count=0, hated_count=0,
        )


def test_format_examples_for_meta_skips_empty():
    """Helper-функция forming meta-prompt не должна возвращать пустые блоки."""
    from router import _format_examples_for_meta
    assert _format_examples_for_meta([], "Тест") == ""
    # Список с пустыми hook → пустой блок
    items = [{"body": {"hook": {"text": ""}}, "rating": 5, "comment": ""}]
    assert _format_examples_for_meta(items, "Тест") == ""

    items = [{"body": {"hook": {"text": "Hook 1"}}, "rating": 5, "comment": "огонь"}]
    out = _format_examples_for_meta(items, "Тест")
    assert "# Тест:" in out
    assert "Hook 1" in out
    assert "огонь" in out
