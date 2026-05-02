"""Тесты feedback persistence — PLAN_SELF_LEARNING_AGENT этап 1."""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from storage import VersionStore  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return VersionStore(tmp_path / "scripts.db")


def _fake_body(hook_text: str = "h") -> dict:
    return {
        "meta": {
            "template": "t", "template_version": "v1", "language": "ru",
            "target_duration_sec": 30, "format": "reels",
        },
        "hook": {"text": hook_text, "estimated_duration_sec": 3.0},
        "body": [{"scene": 1, "text": "s1", "estimated_duration_sec": 10.0, "visual_hint": ""}],
        "cta": {"text": "c", "estimated_duration_sec": 2.0},
        "hashtags": ["#a"],
        "_schema_version": "1.0",
    }


def _create_script(store, hook="h", status="ok"):
    return store.create(
        parent_id=None,
        template="reels_hook_v1",
        template_version="v1",
        schema_version="1.0",
        status=status,
        body=_fake_body(hook),
        params={"topic": "t", "duration_sec": 30},
        profile={},
        constraints_report=None,
        cost_usd=0.01,
        input_tokens=100,
        output_tokens=50,
        latency_ms=200,
        provider="anthropic_claude_text",
        model="claude-sonnet-4-6",
    )


# ─── save / list ─────────────────────────────────────────────────────


def test_save_feedback_stores_all_fields(store):
    s = _create_script(store)
    fid = store.save_feedback(
        script_id=s["id"],
        account_id="acc1",
        rating=5,
        vote="fire",
        comment="отлично",
        refine_request=None,
    )
    assert fid >= 1
    rows = store.list_for_script(s["id"])
    assert len(rows) == 1
    assert rows[0]["rating"] == 5
    assert rows[0]["vote"] == "fire"
    assert rows[0]["comment"] == "отлично"
    assert rows[0]["account_id"] == "acc1"


def test_save_feedback_validates_rating_range(store):
    s = _create_script(store)
    with pytest.raises(ValueError):
        store.save_feedback(script_id=s["id"], rating=6)
    with pytest.raises(ValueError):
        store.save_feedback(script_id=s["id"], rating=0)


def test_save_feedback_validates_vote(store):
    s = _create_script(store)
    with pytest.raises(ValueError):
        store.save_feedback(script_id=s["id"], vote="rocket")


def test_multiple_feedback_events_per_script(store):
    """Пользователь может несколько раз оставить отзыв — все события храним."""
    s = _create_script(store)
    store.save_feedback(script_id=s["id"], account_id="a", rating=3)
    store.save_feedback(script_id=s["id"], account_id="a", rating=4, comment="лучше")
    store.save_feedback(script_id=s["id"], account_id="a", rating=5, comment="идеально")
    rows = store.list_for_script(s["id"])
    assert len(rows) == 3
    # DESC по времени → самое свежее первым
    assert rows[0]["rating"] == 5


# ─── list_for_account ────────────────────────────────────────────────


def test_list_for_account_filters_by_rating_range(store):
    s1 = _create_script(store, "h1")
    s2 = _create_script(store, "h2")
    s3 = _create_script(store, "h3")
    store.save_feedback(script_id=s1["id"], account_id="acc", rating=5)
    store.save_feedback(script_id=s2["id"], account_id="acc", rating=2)
    store.save_feedback(script_id=s3["id"], account_id="acc", rating=4)

    high = store.list_for_account("acc", days=30, min_rating=4)
    assert len(high) == 2
    assert all(r["rating"] >= 4 for r in high)

    low = store.list_for_account("acc", days=30, max_rating=2)
    assert len(low) == 1
    assert low[0]["rating"] == 2


def test_list_for_account_isolates_by_account(store):
    s = _create_script(store)
    store.save_feedback(script_id=s["id"], account_id="acc_a", rating=5)
    store.save_feedback(script_id=s["id"], account_id="acc_b", rating=1)
    a = store.list_for_account("acc_a", days=30)
    b = store.list_for_account("acc_b", days=30)
    assert len(a) == 1 and a[0]["rating"] == 5
    assert len(b) == 1 and b[0]["rating"] == 1


# ─── top_rated / bottom_rated (для context-builder-а) ───────────────


def test_top_rated_for_account_returns_with_body(store):
    s_loved = _create_script(store, hook="GREAT HOOK")
    s_hated = _create_script(store, hook="BAD HOOK")
    store.save_feedback(script_id=s_loved["id"], account_id="acc", rating=5)
    store.save_feedback(script_id=s_hated["id"], account_id="acc", rating=1)

    top = store.top_rated_for_account("acc", limit=3, min_rating=4)
    assert len(top) == 1
    assert top[0]["body"]["hook"]["text"] == "GREAT HOOK"
    assert top[0]["rating"] == 5

    bot = store.bottom_rated_for_account("acc", limit=3, max_rating=2)
    assert len(bot) == 1
    assert bot[0]["body"]["hook"]["text"] == "BAD HOOK"


def test_top_rated_excludes_failed_scripts(store):
    """Скрипты со status != 'ok' не должны попасть в few-shot."""
    s_failed = _create_script(store, hook="BROKEN", status="error")
    store.save_feedback(script_id=s_failed["id"], account_id="acc", rating=5)
    top = store.top_rated_for_account("acc", limit=3, min_rating=4)
    assert top == []


def test_top_rated_orders_by_rating_then_recency(store):
    s5_old = _create_script(store, hook="A")
    s5_new = _create_script(store, hook="B")
    s4 = _create_script(store, hook="C")
    store.save_feedback(script_id=s5_old["id"], account_id="acc", rating=5)
    store.save_feedback(script_id=s5_new["id"], account_id="acc", rating=5)
    store.save_feedback(script_id=s4["id"], account_id="acc", rating=4)

    top = store.top_rated_for_account("acc", limit=3, min_rating=4)
    assert len(top) == 3
    # Сначала ★5, потом ★4
    assert top[0]["rating"] == 5
    assert top[2]["rating"] == 4


# ─── FK cascade ──────────────────────────────────────────────────────


def test_feedback_deleted_with_script(store):
    """ON DELETE CASCADE: при удалении script → feedback тоже исчезает."""
    s = _create_script(store)
    store.save_feedback(script_id=s["id"], account_id="acc", rating=5)
    assert len(store.list_for_script(s["id"])) == 1
    store.delete(s["id"])
    assert len(store.list_for_script(s["id"])) == 0
