from pathlib import Path

from orchestrator.runs.store import RunStore


def test_create_and_get(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    rid = store.create(url="https://x", platform="instagram", external_id="abc")
    run = store.get(rid)
    assert run is not None
    assert run["status"] == "queued"
    assert run["url"] == "https://x"
    assert run["platform"] == "instagram"
    assert run["external_id"] == "abc"
    assert run["steps"] == {}
    assert run["video_meta"] is None


def test_set_status_terminal_sets_finished_at(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    rid = store.create(url="https://x", platform="tiktok")
    store.set_status(rid, "downloading", current_step="download")
    assert store.get(rid)["finished_at"] is None
    store.set_status(rid, "done", result={"k": "v"})
    run = store.get(rid)
    assert run["status"] == "done"
    assert run["finished_at"] is not None
    assert run["result"] == {"k": "v"}


def test_patch_step_merges(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    rid = store.create(url="https://x", platform="instagram")
    store.patch_step(rid, "download", {"status": "running", "started_at": "t0"})
    store.patch_step(rid, "download", {"status": "done", "file_path": "/a"})
    run = store.get(rid)
    dl = run["steps"]["download"]
    # merge: started_at сохранился, status обновился, file_path добавился
    assert dl == {
        "status": "done",
        "started_at": "t0",
        "file_path": "/a",
    }


def test_find_active_by_url_and_video(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    a = store.create(url="https://x", platform="instagram", video_id="vid1")
    # done — не должен матчиться как active
    b = store.create(url="https://x", platform="instagram", video_id="vid1")
    store.set_status(b, "done", result={})

    active = store.find_active_by_video_id("vid1")
    assert active is not None
    assert active["id"] == a

    by_url = store.find_active_by_url("https://x")
    assert by_url["id"] == a


def test_list_stalled(tmp_path: Path):
    import sqlite3
    from datetime import datetime, timedelta, timezone

    store = RunStore(tmp_path / "runs.db")
    rid = store.create(url="https://x", platform="instagram")
    # Сделаем updated_at старым
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    with sqlite3.connect(store.db_path) as c:
        c.execute("UPDATE runs SET updated_at=? WHERE id=?", (old, rid))

    stalled = store.list_stalled(stalled_timeout_sec=300)  # 5 мин
    assert len(stalled) == 1
    assert stalled[0]["id"] == rid

    # Терминальные не возвращаются
    store.set_status(rid, "done", result={})
    assert store.list_stalled(stalled_timeout_sec=300) == []


def test_delete_terminal_older_than_keeps_recent(tmp_path: Path):
    """Удаляются только terminal-runs старше TTL; свежие done/failed остаются."""
    import sqlite3
    from datetime import datetime, timedelta, timezone

    store = RunStore(tmp_path / "runs.db")
    old_done = store.create(url="https://x/1", platform="instagram")
    fresh_done = store.create(url="https://x/2", platform="instagram")
    store.set_status(old_done, "done", result={})
    store.set_status(fresh_done, "done", result={})

    # Старим только old_done (40 дней назад)
    old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    with sqlite3.connect(store.db_path) as c:
        c.execute(
            "UPDATE runs SET finished_at=?, updated_at=? WHERE id=?",
            (old_ts, old_ts, old_done),
        )

    deleted = store.delete_terminal_older_than(ttl_days=30)
    assert deleted == 1
    assert store.get(old_done) is None
    assert store.get(fresh_done) is not None


def test_delete_terminal_older_than_skips_active(tmp_path: Path):
    """Активные runs (queued/downloading/transcribing) не удаляются даже если старые."""
    import sqlite3
    from datetime import datetime, timedelta, timezone

    store = RunStore(tmp_path / "runs.db")
    rid = store.create(url="https://x", platform="instagram")  # queued
    old_ts = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    with sqlite3.connect(store.db_path) as c:
        c.execute("UPDATE runs SET updated_at=? WHERE id=?", (old_ts, rid))

    deleted = store.delete_terminal_older_than(ttl_days=30)
    assert deleted == 0
    assert store.get(rid) is not None  # выжил, потому что queued


def test_delete_terminal_older_than_zero_ttl_noop(tmp_path: Path):
    """ttl_days=0 → ничего не удаляется (защита от случайного truncate всей таблицы)."""
    store = RunStore(tmp_path / "runs.db")
    rid = store.create(url="https://x", platform="instagram")
    store.set_status(rid, "done")
    assert store.delete_terminal_older_than(ttl_days=0) == 0
    assert store.get(rid) is not None


def test_pulse_updates_updated_at_only(tmp_path: Path):
    """heartbeat pulse меняет только updated_at, не трогает status/steps/result."""
    import sqlite3
    import time as time_module
    from datetime import datetime, timedelta, timezone

    store = RunStore(tmp_path / "runs.db")
    rid = store.create(url="https://x", platform="instagram")
    store.set_status(rid, "downloading")
    before = store.get(rid)
    # Старим updated_at искусственно
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with sqlite3.connect(store.db_path) as c:
        c.execute("UPDATE runs SET updated_at=? WHERE id=?", (old, rid))

    time_module.sleep(0.01)
    store.pulse(rid)
    after = store.get(rid)

    # updated_at обновился до сейчас
    assert after["updated_at"] > old
    # Остальные поля без изменений
    assert after["status"] == "downloading"
    assert after["steps"] == before["steps"]
    assert after["finished_at"] is None


def test_pulse_on_unknown_run_does_not_raise(tmp_path: Path):
    """Защита от race-condition: run может быть удалён между heartbeat-tick-ами."""
    store = RunStore(tmp_path / "runs.db")
    # Не падает, просто 0 rows updated
    store.pulse("nonexistent-run-id")


def test_purge_legacy_runs_without_strategy(tmp_path: Path):
    """Миграция: удаляются done/failed без шага strategy в steps_json."""
    store = RunStore(tmp_path / "runs.db")

    # Legacy done — нет strategy → удалить
    legacy_id = store.create(url="https://x/1", platform="instagram")
    store.patch_step(legacy_id, "transcribe", {"status": "done"})
    store.set_status(legacy_id, "done")

    # Новый done — есть strategy → оставить
    new_id = store.create(url="https://x/2", platform="instagram")
    store.patch_step(new_id, "transcribe", {"status": "done"})
    store.patch_step(new_id, "strategy", {"status": "done", "sections": []})
    store.set_status(new_id, "done")

    # Active queued — НЕ удалять никогда
    active_id = store.create(url="https://x/3", platform="instagram")

    deleted = store.purge_legacy_runs_without_strategy()
    assert deleted == 1
    assert store.get(legacy_id) is None
    assert store.get(new_id) is not None
    assert store.get(active_id) is not None  # активный сохранён


def test_purge_legacy_idempotent(tmp_path: Path):
    """Повторный вызов на чистой БД возвращает 0."""
    store = RunStore(tmp_path / "runs.db")
    assert store.purge_legacy_runs_without_strategy() == 0
    rid = store.create(url="https://x", platform="instagram")
    store.patch_step(rid, "strategy", {"status": "done"})
    store.set_status(rid, "done")
    assert store.purge_legacy_runs_without_strategy() == 0  # модерн run сохранён
    assert store.get(rid) is not None
