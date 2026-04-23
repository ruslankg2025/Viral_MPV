from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

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


state = State()
