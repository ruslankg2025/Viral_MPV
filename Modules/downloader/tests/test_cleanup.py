from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cleanup import cleanup_once
from jobs.store import JobStore


def _iso(delta_hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=delta_hours)).isoformat()


def _make_store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.db")


def _insert_failed(store: JobStore, file_path: str | None = None, hours_ago: float = 48.0) -> str:
    job_id = store.create("download", {"url": "https://example.com/v"})
    store.mark_running(job_id)
    result = {"file_path": file_path} if file_path else {}
    store.mark_done(job_id, result)
    # переводим в failed вручную через store, чтобы задать нужный finished_at
    import sqlite3, json
    finished_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE jobs SET status='failed', error='orig_error', finished_at=? WHERE id=?",
            (finished_at, job_id),
        )
        conn.execute(
            "UPDATE jobs SET result_json=? WHERE id=?",
            (json.dumps(result), job_id),
        )
    return job_id


class TestCleanupOnce:
    def test_deletes_file_and_marks_cleaned(self, tmp_path: Path):
        store = _make_store(tmp_path)
        media = tmp_path / "video.mp4"
        media.write_bytes(b"fake")

        job_id = _insert_failed(store, file_path=str(media), hours_ago=48)

        count = cleanup_once(store, ttl_hours=24)

        assert count == 1
        assert not media.exists()
        job = store.get(job_id)
        assert job["error"] == "cleaned_up"
        assert job["status"] == "failed"

    def test_no_file_path_still_marks_cleaned(self, tmp_path: Path):
        store = _make_store(tmp_path)
        job_id = _insert_failed(store, file_path=None, hours_ago=48)

        count = cleanup_once(store, ttl_hours=24)

        assert count == 1
        assert store.get(job_id)["error"] == "cleaned_up"

    def test_missing_file_does_not_raise(self, tmp_path: Path):
        store = _make_store(tmp_path)
        job_id = _insert_failed(store, file_path="/nonexistent/path/video.mp4", hours_ago=48)

        count = cleanup_once(store, ttl_hours=24)

        assert count == 1
        assert store.get(job_id)["error"] == "cleaned_up"

    def test_fresh_failed_job_is_skipped(self, tmp_path: Path):
        store = _make_store(tmp_path)
        media = tmp_path / "fresh.mp4"
        media.write_bytes(b"fake")

        _insert_failed(store, file_path=str(media), hours_ago=1)

        count = cleanup_once(store, ttl_hours=24)

        assert count == 0
        assert media.exists()

    def test_multiple_jobs_all_cleaned(self, tmp_path: Path):
        store = _make_store(tmp_path)
        files = []
        for i in range(3):
            f = tmp_path / f"vid{i}.mp4"
            f.write_bytes(b"x")
            files.append(f)
            _insert_failed(store, file_path=str(f), hours_ago=72)

        count = cleanup_once(store, ttl_hours=24)

        assert count == 3
        assert all(not f.exists() for f in files)

    def test_already_cleaned_job_not_reprocessed(self, tmp_path: Path):
        store = _make_store(tmp_path)
        job_id = _insert_failed(store, file_path=None, hours_ago=48)
        cleanup_once(store, ttl_hours=24)

        # второй запуск: finished_at обновился до "сейчас" при mark_failed
        count = cleanup_once(store, ttl_hours=24)
        assert count == 0
