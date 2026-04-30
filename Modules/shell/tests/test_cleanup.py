"""Тесты cleanup-функции для runs.

Только sync `cleanup_runs_once` — async loop тестируется через test_app
по факту его старта в lifespan.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from orchestrator.cleanup import cleanup_runs_once
from orchestrator.runs.store import RunStore


def _age_run(store: RunStore, run_id: str, *, days_ago: int) -> None:
    """Помечает finished_at + updated_at для теста."""
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    with sqlite3.connect(store.db_path) as c:
        c.execute("UPDATE runs SET finished_at=?, updated_at=? WHERE id=?",
                  (ts, ts, run_id))


def test_cleanup_runs_once_deletes_old_terminal(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    old = store.create(url="https://x/1", platform="instagram")
    fresh = store.create(url="https://x/2", platform="instagram")
    store.set_status(old, "done")
    store.set_status(fresh, "done")
    _age_run(store, old, days_ago=45)

    deleted = cleanup_runs_once(store, ttl_days=30)
    assert deleted == 1
    assert store.get(old) is None
    assert store.get(fresh) is not None


def test_cleanup_runs_once_returns_zero_when_nothing_old(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    rid = store.create(url="https://x", platform="instagram")
    store.set_status(rid, "done")
    assert cleanup_runs_once(store, ttl_days=30) == 0


def test_cleanup_runs_once_handles_failed_status(tmp_path: Path):
    """failed runs тоже подлежат удалению."""
    store = RunStore(tmp_path / "runs.db")
    rid = store.create(url="https://x", platform="instagram")
    store.set_status(rid, "failed", error="boom")
    _age_run(store, rid, days_ago=45)
    assert cleanup_runs_once(store, ttl_days=30) == 1
    assert store.get(rid) is None


def test_cleanup_runs_once_idempotent(tmp_path: Path):
    """Повторный вызов на пустой БД не падает."""
    store = RunStore(tmp_path / "runs.db")
    assert cleanup_runs_once(store, ttl_days=30) == 0
    assert cleanup_runs_once(store, ttl_days=30) == 0
