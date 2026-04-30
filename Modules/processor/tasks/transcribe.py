import json
from pathlib import Path
from typing import Any

from cache.store import build_cache_key
from logging_setup import get_logger
from viral_llm.clients.registry import get_transcription_client
from viral_llm.keys.pricing import estimate_cost
from viral_llm.keys.resolver import KeyResolver, UsageResult
from state import state
from tasks.extract_audio import extract_audio

log = get_logger("tasks.transcribe")


async def run_transcribe(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    file_path = Path(payload["file_path"])
    language = payload.get("language")
    provider = payload.get("provider") or None
    cache_key_base = payload.get("cache_key") or None
    source_ref = payload.get("source_ref") or None

    # v2: cache_key учитывает provider+language, чтобы смена провайдера не давала stale-hit
    cache_key = build_cache_key(
        cache_key_base, provider=provider, language=language
    )

    # Кеш
    if cache_key:
        cached = state.cache_store.get(cache_key, "transcript")
        if cached:
            log.info("transcript_cache_hit", job_id=job_id, cache_key=cache_key)
            return {**cached, "from_cache": True}

    # 1. Извлечение аудио
    audio_path = state.settings.media_dir / "audio" / f"{job_id}.mp3"
    audio = await extract_audio(file_path, audio_path)

    # 2. Выбор провайдера через resolver
    resolver = KeyResolver(state.key_store)

    async def _call(key_record: dict[str, Any], secret: str) -> UsageResult:
        client = get_transcription_client(key_record["provider"])
        tr = await client.transcribe(
            audio_path=audio.path,
            api_key=secret,
            language=language,
        )
        cost = estimate_cost(
            tr.provider, tr.model, audio_seconds=tr.duration_sec or audio.duration_sec
        )
        return UsageResult(
            result=tr,
            provider=tr.provider,
            model=tr.model,
            cost_usd=cost,
            audio_seconds=tr.duration_sec or audio.duration_sec,
            latency_ms=tr.latency_ms,
        )

    usage = await resolver.run_with_fallback(
        kind="transcription",
        job_id=job_id,
        operation="transcribe",
        provider=provider if provider not in (None, "auto") else None,
        call=_call,
    )

    tr = usage.result

    # Сохраняем JSON рядом с аудио (артефакт, на который ссылается analyzer)
    transcripts_dir = state.settings.media_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = transcripts_dir / f"{job_id}.json"

    result: dict[str, Any] = {
        "transcript": {
            "text": tr.text,
            "language": tr.language,
            "provider": tr.provider,
            "model": tr.model,
            "duration_sec": tr.duration_sec or audio.duration_sec,
            "latency_ms": tr.latency_ms,
            "segments": tr.segments,
        },
        "artifacts": {
            "audio_path": str(audio.path),
            "transcript_path": str(transcript_path),
        },
        "cost_usd": {"transcription": round(usage.cost_usd, 6)},
    }
    if source_ref:
        result["source_ref"] = source_ref

    transcript_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if cache_key:
        state.cache_store.set(cache_key, "transcript", result)

    return result
