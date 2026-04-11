from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from config import Settings

if TYPE_CHECKING:
    from cache.store import CacheStore
    from jobs.queue import JobQueue
    from jobs.store import JobStore
    from keys.store import KeyStore


class AppState:
    settings: Settings
    job_store: "JobStore"
    cache_store: "CacheStore"
    key_store: "KeyStore | None" = None
    queue: "JobQueue"


state = AppState()


def db_path(name: str) -> Path:
    return state.settings.db_dir / name
