from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from config import Settings
    from platforms.base import MetricsSource
    from scheduler import SchedulerWrapper
    from storage import MonitorStore


@dataclass
class State:
    settings: Optional["Settings"] = None
    store: Optional["MonitorStore"] = None
    scheduler: Optional["SchedulerWrapper"] = None
    platforms: dict[str, "MetricsSource"] = field(default_factory=dict)
    # async callable для manual trigger через admin endpoint
    watchlist_callback: Optional[Any] = None


state = State()
