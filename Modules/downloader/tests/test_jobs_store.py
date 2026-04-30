from pathlib import Path

from jobs.store import JobStore


def test_create_get_mark_done(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.db")
    job_id = store.create("download", {"url": "https://example.com/x", "platform": "instagram"})

    job = store.get(job_id)
    assert job is not None
    assert job["status"] == "queued"
    assert job["payload"]["url"] == "https://example.com/x"

    store.mark_running(job_id)
    assert store.get(job_id)["status"] == "running"

    store.mark_done(job_id, {"file_path": "/media/downloads/foo.mp4", "sha256": "abc"})
    job = store.get(job_id)
    assert job["status"] == "done"
    assert job["result"]["file_path"] == "/media/downloads/foo.mp4"
    assert job["finished_at"] is not None


def test_mark_failed(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.db")
    job_id = store.create("download", {"url": "https://example.com", "platform": "tiktok"})
    store.mark_failed(job_id, "yt_dlp_failed: 403")
    job = store.get(job_id)
    assert job["status"] == "failed"
    assert job["error"].startswith("yt_dlp_failed")


def test_count_by_status(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.db")
    a = store.create("download", {"url": "https://a", "platform": "instagram"})
    b = store.create("download", {"url": "https://b", "platform": "tiktok"})
    store.mark_done(a, {})
    store.mark_failed(b, "boom")
    counts = store.count_by_status()
    assert counts["done"] == 1
    assert counts["failed"] == 1


def test_list_failed_older_than(tmp_path: Path):
    from datetime import datetime, timedelta, timezone

    store = JobStore(tmp_path / "jobs.db")
    a = store.create("download", {"url": "https://a", "platform": "instagram"})
    store.mark_failed(a, "oops")

    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    assert len(store.list_failed_older_than(future)) == 1
    assert len(store.list_failed_older_than(past)) == 0
