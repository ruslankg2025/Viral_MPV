from __future__ import annotations

from typing import TYPE_CHECKING

from config import Settings

if TYPE_CHECKING:
    from jobs.queue import JobQueue
    from jobs.store import JobStore


class AppState:
    settings: Settings
    job_store: "JobStore"
    queue: "JobQueue"


state = AppState()
