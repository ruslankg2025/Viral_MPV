"""Unit-тесты VersionStore (fork, tree, delete)."""
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


def _fake_body() -> dict:
    return {
        "meta": {
            "template": "t", "template_version": "v1", "language": "ru",
            "target_duration_sec": 30, "format": "reels",
        },
        "hook": {"text": "h", "estimated_duration_sec": 3.0},
        "body": [{"scene": 1, "text": "s1", "estimated_duration_sec": 10.0, "visual_hint": ""}],
        "cta": {"text": "c", "estimated_duration_sec": 2.0},
        "hashtags": ["#a"],
        "_schema_version": "1.0",
    }


def _create(store, parent_id=None):
    return store.create(
        parent_id=parent_id,
        template="reels_hook_v1",
        template_version="v1",
        schema_version="1.0",
        status="ok",
        body=_fake_body(),
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


def test_create_root_version(store):
    v = _create(store)
    assert v["id"] == v["root_id"]
    assert v["parent_id"] is None
    assert v["status"] == "ok"
    assert v["body"]["hook"]["text"] == "h"


def test_fork_inherits_root_id(store):
    root = _create(store)
    fork = _create(store, parent_id=root["id"])
    assert fork["parent_id"] == root["id"]
    assert fork["root_id"] == root["id"]


def test_fork_of_fork_keeps_original_root(store):
    v1 = _create(store)
    v2 = _create(store, parent_id=v1["id"])
    v3 = _create(store, parent_id=v2["id"])
    assert v3["root_id"] == v1["id"]
    assert v3["parent_id"] == v2["id"]


def test_list_tree_returns_all_descendants(store):
    v1 = _create(store)
    v2 = _create(store, parent_id=v1["id"])
    v3 = _create(store, parent_id=v1["id"])
    v4 = _create(store, parent_id=v2["id"])

    tree = store.list_tree(v1["id"])
    ids = {v["id"] for v in tree}
    assert ids == {v1["id"], v2["id"], v3["id"], v4["id"]}


def test_get_children(store):
    v1 = _create(store)
    v2 = _create(store, parent_id=v1["id"])
    v3 = _create(store, parent_id=v1["id"])

    children = store.get_children(v1["id"])
    ids = {c["id"] for c in children}
    assert ids == {v2["id"], v3["id"]}


def test_create_unknown_parent_raises(store):
    with pytest.raises(ValueError, match="parent_not_found"):
        _create(store, parent_id="not-real-uuid")


def test_delete_leaf_ok(store):
    v = _create(store)
    assert store.delete(v["id"]) is True
    assert store.get(v["id"]) is None


def test_cannot_delete_with_children(store):
    v1 = _create(store)
    _create(store, parent_id=v1["id"])
    with pytest.raises(ValueError, match="cannot_delete_version_with_children"):
        store.delete(v1["id"])


def test_delete_unknown_returns_false(store):
    assert store.delete("no-such-id") is False
