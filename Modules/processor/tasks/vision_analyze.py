import json
from pathlib import Path
from typing import Any

from cache.store import build_cache_key
from logging_setup import get_logger
from prompts import get_prompt, get_prompt_record
from viral_llm.clients.registry import get_vision_client
from viral_llm.keys.pricing import estimate_cost
from viral_llm.keys.resolver import KeyResolver, UsageResult
from state import state
from tasks.extract_frames import extract_frames

log = get_logger("tasks.vision_analyze")


async def run_vision_analyze(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    file_path = Path(payload["file_path"])
    provider = payload.get("provider") or None
    prompt_template = payload.get("prompt_template") or "default"
    sampling = payload.get("sampling") or {}
    cache_key_base = payload.get("cache_key") or None
    source_ref = payload.get("source_ref") or None
    requested_prompt_version = payload.get("prompt_version") or None
    analysis_profile = payload.get("analysis_profile") or None

    # v2: получаем тело промпта + фактическую версию через registry
    prompt_record = get_prompt_record(prompt_template, requested_prompt_version)
    prompt = prompt_record.body
    prompt_version = prompt_record.full_version  # например, "vision_default:v1"

    # v2: cache_key учитывает версию промпта, провайдера и профиль анализа
    cache_key = build_cache_key(
        cache_key_base,
        prompt_version=prompt_version,
        provider=provider,
        profile=analysis_profile,
    )

    if cache_key:
        cached = state.cache_store.get(cache_key, "vision")
        if cached:
            log.info("vision_cache_hit", job_id=job_id, cache_key=cache_key)
            return {**cached, "from_cache": True}

    # 1. Извлекаем кадры
    frames_out = state.settings.media_dir / "frames" / job_id
    frames_result = await extract_frames(
        video_path=file_path,
        out_dir=frames_out,
        fps=float(sampling.get("fps", 1.0)),
        diff_threshold=float(sampling.get("diff_threshold", 0.10)),
        min_frames=int(sampling.get("min_frames", 3)),
        max_frames=int(sampling.get("max_frames", 40)),
    )
    frame_paths = [Path(f.file_path) for f in frames_result.extracted]
    if not frame_paths:
        raise RuntimeError("no_frames_extracted")

    # 2. Vision через resolver + fallback chain
    resolver = KeyResolver(state.key_store)

    async def _call(key_record: dict[str, Any], secret: str) -> UsageResult:
        client = get_vision_client(key_record["provider"])
        vr = await client.analyze(
            frame_paths=frame_paths,
            api_key=secret,
            prompt=prompt,
        )
        cost = estimate_cost(
            vr.provider,
            vr.model,
            input_tokens=vr.input_tokens,
            output_tokens=vr.output_tokens,
        )
        return UsageResult(
            result=vr,
            provider=vr.provider,
            model=vr.model,
            cost_usd=cost,
            input_tokens=vr.input_tokens,
            output_tokens=vr.output_tokens,
            frames=len(frame_paths),
            latency_ms=vr.latency_ms,
        )

    usage = await resolver.run_with_fallback(
        kind="vision",
        job_id=job_id,
        operation="vision_analyze",
        provider=provider if provider not in (None, "auto") else None,
        call=_call,
    )

    vr = usage.result

    # v2: сериализуем vision-блок на диск — analyzer прочитает его по пути
    vision_dir = state.settings.media_dir / "vision"
    vision_dir.mkdir(parents=True, exist_ok=True)
    vision_result_path = vision_dir / f"{job_id}.json"

    result: dict[str, Any] = {
        "prompt_version": prompt_version,
        "frames": {
            "extracted": [
                {
                    "index": f.index,
                    "timestamp_sec": f.timestamp_sec,
                    "file_path": f.file_path,
                    "diff_ratio": f.diff_ratio,
                }
                for f in frames_result.extracted
            ],
            "stats": frames_result.stats,
        },
        "vision": {
            "provider": vr.provider,
            "model": vr.model,
            "prompt_template": prompt_template,
            "prompt_version": prompt_version,
            **vr.raw_json,
            "input_tokens": vr.input_tokens,
            "output_tokens": vr.output_tokens,
            "latency_ms": vr.latency_ms,
        },
        "artifacts": {
            "frames_dir": str(frames_out),
            "vision_result_path": str(vision_result_path),
        },
        "cost_usd": {"vision": round(usage.cost_usd, 6)},
    }
    if source_ref:
        result["source_ref"] = source_ref

    vision_result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if cache_key:
        state.cache_store.set(cache_key, "vision", result)

    return result
