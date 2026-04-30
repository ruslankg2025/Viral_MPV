"""TTL cleanup для runs — удаляет terminal runs (done/failed) старше N дней.

Активные runs никогда не трогаются: они либо в процессе, либо реально зависли
(в этом случае сигнализируют о проблеме, а не о мусоре в БД).
"""
import asyncio
from contextlib import suppress

from orchestrator.config import OrchestratorSettings
from orchestrator.logging_setup import get_logger
from orchestrator.runs.store import RunStore

log = get_logger("cleanup")


def cleanup_runs_once(store: RunStore, ttl_days: int) -> int:
    """Один проход: удалить terminal runs старше ttl_days. Возвращает кол-во удалённых."""
    deleted = store.delete_terminal_older_than(ttl_days)
    if deleted:
        log.info("runs_cleanup_done", deleted=deleted, ttl_days=ttl_days)
    return deleted


async def run_cleanup_loop(store: RunStore, settings: OrchestratorSettings) -> None:
    """Background-loop: каждые orchestrator_cleanup_interval_sec выполняет cleanup_runs_once.

    Никогда не падает наружу — все ошибки логируются и цикл продолжается.
    """
    interval = settings.orchestrator_cleanup_interval_sec
    ttl = settings.orchestrator_runs_ttl_days
    log.info("runs_cleanup_loop_started", interval_sec=interval, ttl_days=ttl)
    while True:
        await asyncio.sleep(interval)
        try:
            cleanup_runs_once(store, ttl)
        except Exception:
            log.exception("runs_cleanup_error")
