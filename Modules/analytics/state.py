"""Singleton state analytics-сервиса."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import Settings
    from publisher_client import PublisherClient
    from storage import AnalyticsStore


class AppState:
    settings: "Settings | None" = None
    store: "AnalyticsStore | None" = None
    publisher_client: "PublisherClient | None" = None
    vk_token: str | None = None


state = AppState()
