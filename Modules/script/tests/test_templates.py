"""Unit-тесты TemplateStore + bootstrap_builtin_templates."""
import sys
from pathlib import Path

import pytest

# Script-модули ожидают, что корень /app в sys.path (как в контейнере)
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from templates import TemplateStore, bootstrap_builtin_templates  # noqa: E402
from builtin_templates import BUILTIN_TEMPLATES  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return TemplateStore(tmp_path / "templates.db")


def test_bootstrap_creates_all_builtin(store):
    created = bootstrap_builtin_templates(store)
    assert created == len(BUILTIN_TEMPLATES)

    for name in BUILTIN_TEMPLATES:
        rec = store.get(name)
        assert rec is not None
        assert rec.version == "v1"
        assert rec.is_active is True
        assert rec.body == BUILTIN_TEMPLATES[name]


def test_bootstrap_is_idempotent(store):
    bootstrap_builtin_templates(store)
    second = bootstrap_builtin_templates(store)
    assert second == 0


def test_create_and_get(store):
    store.create(name="custom", version="v1", body="hello", is_active=True)
    rec = store.get("custom")
    assert rec is not None
    assert rec.body == "hello"
    assert rec.full_version == "custom:v1"


def test_activate_deactivates_previous(store):
    store.create(name="custom", version="v1", body="b1", is_active=True)
    store.create(name="custom", version="v2", body="b2", is_active=False)
    store.activate("custom", "v2")

    versions = store.list_versions("custom")
    active = [v for v in versions if v["is_active"]]
    assert len(active) == 1
    assert active[0]["version"] == "v2"


def test_cannot_delete_active_version(store):
    store.create(name="custom", version="v1", body="b1", is_active=True)
    with pytest.raises(ValueError, match="cannot_delete_active"):
        store.delete("custom", "v1")


def test_delete_inactive_ok(store):
    store.create(name="custom", version="v1", body="b1", is_active=True)
    store.create(name="custom", version="v2", body="b2", is_active=False)
    assert store.delete("custom", "v2") is True
    versions = store.list_versions("custom")
    assert len(versions) == 1


def test_get_unknown_returns_none(store):
    assert store.get("nonexistent") is None


def test_list_versions_unknown_empty(store):
    assert store.list_versions("nonexistent") == []
