"""Тесты POST /script/{id}/refine — этап 6 self-learning agent.

Покрываем чисто валидацию входа и shape ответа без реального LLM-call —
для последнего нужен fake_llm-фикстур, который уже есть в test_router.py.
Здесь — direct unit-уровень (валидация action, custom_text для free)."""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from schemas import RefineReq  # noqa: E402


def test_refine_req_validates_known_actions():
    for a in ("amplify_hook", "shorten", "rewrite_intro", "simplify", "free"):
        r = RefineReq(action=a)
        assert r.action == a


def test_refine_req_rejects_unknown_action():
    with pytest.raises(Exception):
        RefineReq(action="explode_universe")


def test_refine_req_custom_text_optional():
    r = RefineReq(action="amplify_hook")
    assert r.custom_text is None


def test_refine_req_custom_text_max_length():
    """Pydantic Field max_length=1000 должен валидировать длину."""
    long = "a" * 1001
    with pytest.raises(Exception):
        RefineReq(action="free", custom_text=long)


def test_refine_instructions_keys_match_actions():
    """_REFINE_INSTRUCTIONS должен покрывать все non-free actions."""
    from router import _REFINE_INSTRUCTIONS
    expected = {"amplify_hook", "shorten", "rewrite_intro", "simplify"}
    assert set(_REFINE_INSTRUCTIONS.keys()) == expected
