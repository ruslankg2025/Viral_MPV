import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import get_settings
from jobs.store import JobStore
from logging_setup import get_logger

log = get_logger("cleanup")


def cleanup_once(store: JobStore, ttl_hours: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ttl_hours)).isoformat()
    jobs = store.list_failed_older_than(cutoff)
    for job in jobs:
        result = job.get("result") or {}
        file_path = result.get("file_path")
        if file_path:
            p = Path(file_path)
            with suppress(FileNotFoundError):
                p.unlink()
                log.info("cleanup_file_deleted", job_id=job["id"], path=file_path)
        store.mark_failed(job["id"], "cleaned_up")
    if jobs:
        log.info("cleanup_done", count=len(jobs))
    return len(jobs)


async def run_cleanup_loop(store: JobStore) -> None:
    settings = get_settings()
    while True:
        await asyncio.sleep(3600)
        try:
            cleanup_once(store, settings.ttl_failed_hours)
        except Exception:
            log.exception("cleanup_error")
