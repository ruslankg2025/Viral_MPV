from __future__ import annotations

from typing import TYPE_CHECKING

from config import Settings

if TYPE_CHECKING:
    from storage import VersionStore
    from templates import TemplateStore
    from viral_llm.keys.store import KeyStore


class AppState:
    settings: Settings
    key_store: "KeyStore | None" = None
    template_store: "TemplateStore | None" = None
    version_store: "VersionStore | None" = None


state = AppState()
