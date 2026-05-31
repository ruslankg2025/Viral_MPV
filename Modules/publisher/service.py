"""Бизнес-логика публикации — общая для router (publish/schedule/retry) и
для фонового scheduler-а. Решает dry-run vs live и обновляет статусы в БД.
"""
from __future__ import annotations

from typing import Any

from config import Settings
from dry_run import simulate_publish
from logging_setup import get_logger
from storage import PublicationStore
from vk_client import VKClient, VKError

log = get_logger()


def resolve_dry_run(req_dry_run: bool | None, settings: Settings) -> bool:
    """dry_run=true в запросе ИЛИ DEFAULT_DRY_RUN=true → dry-run."""
    if req_dry_run is True:
        return True
    if req_dry_run is None and settings.default_dry_run:
        return True
    if req_dry_run is False:
        return False
    # req_dry_run is None и default_dry_run=False
    return False


def _make_vk_client(settings: Settings) -> VKClient:
    return VKClient(
        access_token=settings.vk_access_token,
        api_version=settings.vk_api_version,
        group_id=settings.vk_group_id,
    )


async def execute_publication(
    pub: dict[str, Any],
    *,
    store: PublicationStore,
    settings: Settings,
    dry_run: bool,
) -> dict[str, Any]:
    """Опубликовать одну запись publication (уже созданную в БД).

    dry_run=True  → НЕ ходим в api.vk.com, ставим status='dry_run'.
    dry_run=False → live через VKClient, status publishing→published|failed.
    Возвращает обновлённую запись.
    """
    pid = pub["id"]
    platform = pub["platform"]
    title = pub["title"]
    description = pub.get("description", "")
    tags = pub.get("tags", [])
    video_path = pub.get("video_path")

    if dry_run:
        sim = simulate_publish(
            platform=platform,
            video_path=video_path,
            title=title,
            description=description,
            tags=tags,
        )
        return store.set_status(
            pid,
            status="dry_run",
            external_id=sim["external_id"],
            error_message=None,
        )

    # ── live ──────────────────────────────────────────────────────────────────
    store.set_status(pid, status="publishing", error_message=None)
    try:
        client = _make_vk_client(settings)
        if platform == "vk_clips":
            result = await client.publish_clip(
                video_path=video_path, title=title, description=description, tags=tags
            )
        else:  # vk_video
            result = await client.publish_video(
                video_path=video_path, title=title, description=description, tags=tags
            )
        updated = store.set_status(
            pid,
            status="published",
            external_id=result.get("external_id"),
            error_message=None,
            mark_published=True,
        )
        log.info("publish_live_ok", publication_id=pid, platform=platform,
                 external_id=result.get("external_id"))
        return updated
    except (VKError, OSError, Exception) as exc:  # noqa: BLE001 — фиксируем любую ошибку в БД
        log.warning("publish_live_failed", publication_id=pid, platform=platform,
                    error=str(exc))
        return store.set_status(pid, status="failed", error_message=str(exc))
