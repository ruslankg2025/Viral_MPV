"""Тесты context-builder-а: few-shot из feedback-store
(PLAN_SELF_LEARNING_AGENT этап 3)."""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from generator import GenContext, _build_feedback_fewshot, _build_user_prompt  # noqa: E402
from schemas import GenerateParams  # noqa: E402
from storage import VersionStore  # noqa: E402


def _ctx(account_id=None, store=None) -> GenContext:
    return GenContext(
        template_name="reels_hook_v1",
        template_version="v1",
        template_body="System prompt body",
        params=GenerateParams(topic="Привычки", duration_sec=30),
        profile={},
        provider=None,
        account_id=account_id,
        feedback_store=store,
    )


def _seed_script(store: VersionStore, hook: str, status: str = "ok") -> str:
    rec = store.create(
        parent_id=None,
        template="reels_hook_v1",
        template_version="v1",
        schema_version="1.0",
        status=status,
        body={
            "meta": {"template": "t", "template_version": "v1", "language": "ru",
                     "target_duration_sec": 30, "format": "reels"},
            "hook": {"text": hook, "estimated_duration_sec": 3.0},
            "body": [{"scene": 1, "text": "s", "estimated_duration_sec": 10.0, "visual_hint": ""}],
            "cta": {"text": "c", "estimated_duration_sec": 2.0},
            "hashtags": ["#a"],
            "_schema_version": "1.0",
        },
        params={"topic": "t", "duration_sec": 30},
        profile={},
        constraints_report=None,
        cost_usd=0.0, input_tokens=10, output_tokens=10, latency_ms=100,
        provider="anthropic_claude_text",
        model="claude-sonnet-4-6",
    )
    return rec["id"]


# ─── Базовые случаи ──────────────────────────────────────────────────


def test_no_account_no_store_returns_empty(tmp_path):
    """Без account_id или store → блок не подмешивается."""
    assert _build_feedback_fewshot(_ctx()) == ""

    store = VersionStore(tmp_path / "scripts.db")
    assert _build_feedback_fewshot(_ctx(account_id="acc", store=None)) == ""
    assert _build_feedback_fewshot(_ctx(account_id=None, store=store)) == ""


def test_no_feedback_returns_empty(tmp_path):
    """account_id есть, но feedback пуст → пустая строка."""
    store = VersionStore(tmp_path / "scripts.db")
    out = _build_feedback_fewshot(_ctx(account_id="acc", store=store))
    assert out == ""


# ─── Loved-only ─────────────────────────────────────────────────────


def test_only_loved_examples_in_prompt(tmp_path):
    store = VersionStore(tmp_path / "scripts.db")
    sid = _seed_script(store, "Топ-3 факта про инвестиции")
    store.save_feedback(script_id=sid, account_id="acc", rating=5, comment="огонь")

    out = _build_feedback_fewshot(_ctx(account_id="acc", store=store))
    assert "НРАВИЛИСЬ" in out
    assert "Топ-3 факта про инвестиции" in out
    assert "огонь" in out
    assert "НЕ ПОНРАВИЛИСЬ" not in out


# ─── Hated-only ─────────────────────────────────────────────────────


def test_only_hated_examples_in_prompt(tmp_path):
    store = VersionStore(tmp_path / "scripts.db")
    sid = _seed_script(store, "Скучный hook")
    store.save_feedback(script_id=sid, account_id="acc", rating=1, comment="вода")

    out = _build_feedback_fewshot(_ctx(account_id="acc", store=store))
    assert "НЕ ПОНРАВИЛИСЬ" in out
    assert "Скучный hook" in out
    assert "вода" in out
    assert "НРАВИЛИСЬ" not in out.split("НЕ ПОНРАВИЛИСЬ")[0]


# ─── Both blocks ─────────────────────────────────────────────────────


def test_both_loved_and_hated(tmp_path):
    store = VersionStore(tmp_path / "scripts.db")
    s1 = _seed_script(store, "ХОРОШИЙ HOOK")
    s2 = _seed_script(store, "ПЛОХОЙ HOOK")
    store.save_feedback(script_id=s1, account_id="acc", rating=5)
    store.save_feedback(script_id=s2, account_id="acc", rating=1)

    out = _build_feedback_fewshot(_ctx(account_id="acc", store=store))
    assert "НРАВИЛИСЬ" in out
    assert "НЕ ПОНРАВИЛИСЬ" in out
    assert "ХОРОШИЙ HOOK" in out
    assert "ПЛОХОЙ HOOK" in out


# ─── Account isolation ──────────────────────────────────────────────


def test_account_isolation(tmp_path):
    """Feedback другого аккаунта не должен подмешиваться."""
    store = VersionStore(tmp_path / "scripts.db")
    sa = _seed_script(store, "USER_A_HOOK")
    sb = _seed_script(store, "USER_B_HOOK")
    store.save_feedback(script_id=sa, account_id="acc_a", rating=5)
    store.save_feedback(script_id=sb, account_id="acc_b", rating=5)

    out_a = _build_feedback_fewshot(_ctx(account_id="acc_a", store=store))
    assert "USER_A_HOOK" in out_a
    assert "USER_B_HOOK" not in out_a


# ─── Store error → graceful (не должен ломать generation) ──────────


def test_store_error_returns_empty():
    """Если store бросает exception — feedback опционален, не блокируем."""
    class BrokenStore:
        def top_rated_for_account(self, *a, **kw): raise RuntimeError("boom")
        def bottom_rated_for_account(self, *a, **kw): raise RuntimeError("boom")

    out = _build_feedback_fewshot(_ctx(account_id="acc", store=BrokenStore()))
    assert out == ""


# ─── Integration: _build_user_prompt видит блок ─────────────────────


def test_build_user_prompt_includes_feedback_block(tmp_path):
    """Полный prompt-builder должен включить few-shot после Profile."""
    store = VersionStore(tmp_path / "scripts.db")
    sid = _seed_script(store, "Мощный hook")
    store.save_feedback(script_id=sid, account_id="acc", rating=5)

    prompt = _build_user_prompt(_ctx(account_id="acc", store=store))
    assert "Topic: Привычки" in prompt
    assert "НРАВИЛИСЬ" in prompt
    assert "Мощный hook" in prompt


def test_build_user_prompt_no_feedback_clean(tmp_path):
    """Без feedback prompt тот же что и раньше — не сломали legacy."""
    store = VersionStore(tmp_path / "scripts.db")
    prompt = _build_user_prompt(_ctx(account_id="acc", store=store))
    assert "Topic: Привычки" in prompt
    assert "НРАВИЛИСЬ" not in prompt
    assert "НЕ ПОНРАВИЛИСЬ" not in prompt
