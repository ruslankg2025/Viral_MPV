from orchestrator.logging_setup import get_logger
from orchestrator.runs.store import RunStore

log = get_logger("recovery")


def recover_stalled_runs(store: RunStore, stalled_timeout_sec: int) -> int:
    """При старте сервиса перевести все non-terminal run-ы, не обновлявшиеся
    дольше stalled_timeout_sec, в status='failed' с error='stalled_after_crash'.

    Защищает от ситуации, когда shell упал между шагами pipeline и run застрял
    в статусе 'downloading'/'analyzing' навсегда.

    Возвращает число переведённых run-ов.
    """
    stalled = store.list_stalled(stalled_timeout_sec)
    for run in stalled:
        store.set_status(
            run["id"],
            "failed",
            current_step=run.get("current_step"),
            error="stalled_after_crash",
        )
        log.warning(
            "run_recovered_to_failed",
            run_id=run["id"],
            previous_status=run["status"],
            current_step=run.get("current_step"),
        )
    if stalled:
        log.info("recovery_complete", recovered=len(stalled))
    return len(stalled)
