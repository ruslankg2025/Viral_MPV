from __future__ import annotations

from typing import TYPE_CHECKING

from config import Settings

if TYPE_CHECKING:
    from storage import ProfileStore


class AppState:
    settings: Settings
    profile_store: "ProfileStore | None" = None


state = AppState()
