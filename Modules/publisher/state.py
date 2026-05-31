from __future__ import annotations

from typing import TYPE_CHECKING

from config import Settings

if TYPE_CHECKING:
    from storage import PublicationStore


class AppState:
    settings: Settings
    publication_store: "PublicationStore | None" = None


state = AppState()
