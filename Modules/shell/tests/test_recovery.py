import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from orchestrator.recovery import recover_stalled_runs
from orchestrator.runs.store import RunStore


def _backdate(store: RunStore, run_id: str, minutes_ago: int) -> None:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    with sqlite3.connect(store.db_path) as c:
        c.execute("UPDATE runs SET updated_at=? WHERE id=?", (ts, run_id))


def test_recovery_marks_stalled_as_failed(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    a = store.create(url="https://1", platform="instagram")
    store.set_status(a, "downloading", current_step="download")
    _backdate(store, a, minutes_ago=10)

    b = store.create(url="https://2", platform="tiktok")
    # b — свежий, не должен трогаться

    n = recover_stalled_runs(store, stalled_timeout_sec=300)
    assert n == 1
    assert store.get(a)["status"] == "failed"
    assert store.get(a)["error"] == "stalled_after_crash"
    assert store.get(b)["status"] == "queued"


def test_recovery_idempotent(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    a = store.create(url="https://1", platform="instagram")
    store.set_status(a, "downloading", current_step="download")
    _backdate(store, a, minutes_ago=10)

    assert recover_stalled_runs(store, stalled_timeout_sec=300) == 1
    # Второй вызов — ничего не находит
    assert recover_stalled_runs(store, stalled_timeout_sec=300) == 0
    assert store.get(a)["status"] == "failed"


def test_recovery_skips_done_and_failed(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    a = store.create(url="https://1", platform="instagram")
    store.set_status(a, "done", result={})
    _backdate(store, a, minutes_ago=10)

    b = store.create(url="https://2", platform="instagram")
    store.set_status(b, "failed", error="boom")
    _backdate(store, b, minutes_ago=10)

    assert recover_stalled_runs(store, stalled_timeout_sec=300) == 0
