"""Job handlers. На Этапе 2 содержат заглушки — реальные реализации
подключаются в последующих этапах (см. tasks/transcribe.py, tasks/extract_frames.py и т.д.)."""

from typing import Any

from logging_setup import get_logger

log = get_logger("tasks")


async def stub_handler(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    log.info("stub_handler", job_id=job_id, payload_keys=list(payload))
    return {"stub": True, "payload_echo": payload}
