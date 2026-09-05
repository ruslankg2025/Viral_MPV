"""Разделяемое состояние процесса (как publisher/state.py)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from config import Settings
    from storage import JobStore


class _State:
    settings: "Optional[Settings]" = None
    job_store: "Optional[JobStore]" = None


state = _State()
