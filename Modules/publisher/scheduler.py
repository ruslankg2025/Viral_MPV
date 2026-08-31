"""Фоновая очередь отложенных публикаций.

Раз в минуту берёт записи со status='scheduled' и scheduled_at<=NOW и публикует
через service.execute_publication. Запускается как asyncio.Task в lifespan.
"""
from __future__ import annotations

import asyncio

from config import Settings
from logging_setup import get_logger
from service import execute_publication
from storage import PublicationStore

log = get_logger()

POLL_INTERVAL_SEC = 60


async def run_scheduler(store: PublicationStore, settings: Settings) -> None:
    log.info("scheduler_started", poll_interval_sec=POLL_INTERVAL_SEC)
    try:
        while True:
            try:
                await _tick(store, settings)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — цикл не должен падать
                log.warning("scheduler_tick_error", error=str(exc))
            await asyncio.sleep(POLL_INTERVAL_SEC)
    except asyncio.CancelledError:
        log.info("scheduler_stopped")
        raise


async def _tick(store: PublicationStore, settings: Settings) -> None:
    due = store.due_scheduled()
    if not due:
        return
    log.info("scheduler_due", count=len(due))
    for pub in due:
        # Защита от двойного захвата: переводим в publishing до публикации.
        store.set_status(pub["id"], status="publishing", error_message=None)
        dry = settings.default_dry_run
        await execute_publication(pub, store=store, settings=settings, dry_run=dry)
