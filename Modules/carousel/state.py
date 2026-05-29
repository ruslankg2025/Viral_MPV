from __future__ import annotations

from typing import TYPE_CHECKING

from config import Settings

if TYPE_CHECKING:
    from storage import CarouselStore, TemplateStore
    from viral_llm.keys.store import KeyStore


class AppState:
    settings: Settings
    key_store: "KeyStore | None" = None
    template_store: "TemplateStore | None" = None
    carousel_store: "CarouselStore | None" = None


state = AppState()
