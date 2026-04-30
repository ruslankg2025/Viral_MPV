"""Single-flight dedup: если для того же видео уже есть active run — возвращаем его id."""
from typing import Any

from orchestrator.runs.store import RunStore


def find_active_duplicate(
    store: RunStore, *, video_id: str | None, url: str
) -> dict[str, Any] | None:
    """Возвращает active run, если он уже существует для того же видео.

    Приоритет: video_id (уникальный ключ из monitor) > url.
    """
    if video_id:
        existing = store.find_active_by_video_id(video_id)
        if existing:
            return existing
    return store.find_active_by_url(url)
