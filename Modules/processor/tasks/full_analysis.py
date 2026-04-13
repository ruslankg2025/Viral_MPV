"""Полный анализ: параллельно транскрипция (audio+transcribe)
и цепочка (extract_frames -> vision_analyze)."""

import asyncio
from pathlib import Path
from typing import Any

from cache.store import build_cache_key
from logging_setup import get_logger
from schemas.result_v2 import ANALYSIS_VERSION
from state import state
from tasks.extract_frames import extract_frames
from tasks.transcribe import run_transcribe
from tasks.vision_analyze import run_vision_analyze

log = get_logger("tasks.full_analysis")


async def run_full_analysis(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    file_path = Path(payload["file_path"])
    sampling = payload.get("sampling") or {}

    # v2: providers — раздельный блок имеет приоритет над плоскими полями
    providers = payload.get("providers") or {}
    transcribe_provider = (
        providers.get("transcription") if providers else None
    ) or payload.get("transcribe_provider")
    vision_provider = (
        providers.get("vision") if providers else None
    ) or payload.get("vision_provider")

    source_ref = payload.get("source_ref") or None
    prompt_version = payload.get("prompt_version") or None
    prompt_template = payload.get("prompt_template") or "default"
    analysis_profile = payload.get("analysis_profile") or None
    cache_key_base = payload.get("cache_key")

    # v2: эффективный cache_key учитывает все версии
    cache_key = build_cache_key(
        cache_key_base,
        prompt_version=prompt_version,
        prompt_template=prompt_template,
        vision=vision_provider,
        transcription=transcribe_provider,
        profile=analysis_profile,
    )

    if cache_key:
        cached = state.cache_store.get(cache_key, "full_analysis")
        if cached:
            log.info("full_analysis_cache_hit", job_id=job_id, cache_key=cache_key)
            return {**cached, "from_cache": True}

    transcribe_payload = {
        "file_path": str(file_path),
        "provider": transcribe_provider,
        "language": payload.get("language"),
        "source_ref": source_ref,
    }
    vision_payload = {
        "file_path": str(file_path),
        "provider": vision_provider,
        "sampling": sampling,
        "prompt_template": prompt_template,
        "prompt_version": prompt_version,
        "analysis_profile": analysis_profile,
        "source_ref": source_ref,
    }

    transcribe_task = asyncio.create_task(
        _safe_run(run_transcribe, f"{job_id}_tr", transcribe_payload),
        name=f"{job_id}-transcribe",
    )
    vision_task = asyncio.create_task(
        _safe_run(run_vision_analyze, f"{job_id}_vi", vision_payload),
        name=f"{job_id}-vision",
    )

    tr_res, tr_err = await transcribe_task
    vi_res, vi_err = await vision_task

    # Оба упали — падаем
    if tr_err and vi_err:
        raise RuntimeError(
            f"full_analysis_failed: transcribe={tr_err}; vision={vi_err}"
        )

    result: dict[str, Any] = {
        "analysis_version": ANALYSIS_VERSION,
    }
    artifacts: dict[str, str] = {}
    cost_parts: dict[str, float] = {}

    if tr_res:
        result["transcript"] = tr_res.get("transcript")
        cost_parts["transcription"] = tr_res.get("cost_usd", {}).get("transcription", 0.0)
        artifacts.update(tr_res.get("artifacts") or {})
    elif tr_err:
        result["transcript_error"] = tr_err

    if vi_res:
        result["frames"] = vi_res.get("frames")
        result["vision"] = vi_res.get("vision")
        cost_parts["vision"] = vi_res.get("cost_usd", {}).get("vision", 0.0)
        artifacts.update(vi_res.get("artifacts") or {})
        # Используем фактическую prompt_version, проставленную vision-task
        if vi_res.get("prompt_version"):
            result["prompt_version"] = vi_res["prompt_version"]
    elif vi_err:
        result["vision_error"] = vi_err

    cost_parts["total"] = round(sum(cost_parts.values()), 6)
    result["cost_usd"] = {k: round(v, 6) for k, v in cost_parts.items()}

    if artifacts:
        result["artifacts"] = artifacts
    if source_ref:
        result["source_ref"] = source_ref

    if cache_key:
        state.cache_store.set(cache_key, "full_analysis", result)

    return result


async def _safe_run(fn, sub_id: str, payload: dict[str, Any]):
    try:
        res = await fn(sub_id, payload)
        return res, None
    except Exception as e:
        log.exception("full_analysis_subtask_failed", sub_id=sub_id)
        return None, f"{type(e).__name__}: {e}"
