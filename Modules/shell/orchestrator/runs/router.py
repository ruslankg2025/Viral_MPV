import os
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl

from orchestrator.clients.monitor import MonitorError
from orchestrator.clients.script import ScriptError
from orchestrator.dedup import find_active_duplicate
from orchestrator.logging_setup import get_logger
from orchestrator.state import state

log = get_logger("runs.router")

router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])


Platform = Literal["instagram", "tiktok", "youtube_shorts"]


class CreateRunReq(BaseModel):
    """Два режима:

    1. **Из Мониторинга** (UI): передан только `video_id` (UUID из monitor.videos).
       Orchestrator сам подтянет `url`, `platform`, `external_id` через monitor-client
       и закэширует в `runs.video_meta_json` (decoupling от monitor на runtime).

    2. **Прямой** (тесты, ручные API-вызовы): переданы `url` + `platform` (опц. external_id).
       Используется когда видео нет в monitor.

    Если переданы и video_id, и url — приоритет у переданных полей; video_id
    идёт в дедуп и в metadata.
    """
    url: HttpUrl | None = None
    platform: Platform | None = None
    external_id: str | None = Field(
        default=None,
        description="Для cache_key: 'instagram:abc123' → processor cache hit при reanalyze",
    )
    video_id: str | None = Field(
        default=None,
        description="UUID из monitor.videos — orchestrator подтянет metadata",
    )
    account_id: str | None = Field(
        default=None,
        description="Если задан — инжектирует brand_book/prompt_profile в script-gen",
    )
    script_template: str | None = Field(
        default=None,
        description="reels_standard|shorts_hook|long — шаблон для генерации сценария",
    )


@router.post("/runs", status_code=202)
async def create_run(req: CreateRunReq):
    if not req.video_id and not (req.url and req.platform):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="missing_inputs: provide either video_id OR (url + platform)",
        )

    # Lookup из monitor если url не передан
    video_meta: dict | None = None
    url_str: str
    platform: str
    external_id: str | None
    if req.url and req.platform:
        url_str = str(req.url)
        platform = req.platform
        external_id = req.external_id
    else:
        try:
            video_meta = await state.monitor_client.get_video(req.video_id)  # type: ignore[arg-type]
        except MonitorError as e:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=f"monitor_lookup_failed: {e}",
            )
        url_str = video_meta["url"]
        platform = video_meta["platform"]
        external_id = video_meta.get("external_id") or req.external_id

        if platform not in ("instagram", "tiktok", "youtube_shorts"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"unsupported_platform_in_monitor: {platform}",
            )

    existing = find_active_duplicate(
        state.run_store, video_id=req.video_id, url=url_str
    )
    if existing:
        log.info(
            "single_flight_hit",
            existing_run_id=existing["id"],
            video_id=req.video_id,
            url=url_str,
        )
        return {
            "run_id": existing["id"],
            "status": existing["status"],
            "deduped": True,
        }

    run_id = state.run_store.create(
        url=url_str,
        platform=platform,
        external_id=external_id,
        video_id=req.video_id,
        account_id=req.account_id,
        script_template=req.script_template,
    )
    if video_meta is not None:
        # Кэшируем metadata в самой записи runs — больше не дёргаем monitor
        # на этом run-е (защита от monitor downtime во время pipeline).
        state.run_store.set_video_meta(run_id, video_meta)
    state.runner.kick_off(run_id)
    log.info(
        "run_created",
        run_id=run_id,
        platform=platform,
        external_id=external_id,
        from_video_id=req.video_id,
    )
    return {
        "run_id": run_id,
        "status": "queued",
        "pipeline": ["download", "analyze"],
    }


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    run = state.run_store.get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="run_not_found")
    return run


@router.get("/runs")
async def list_runs(video_id: str | None = None, limit: int = 50):
    if video_id:
        return state.run_store.list_by_video(video_id, limit=limit)
    return state.run_store.list_recent(limit=limit)


# ---------- "Создать аналог" — on-demand script generation ----------

_PLATFORM_FORMAT_MAP = {
    "youtube_shorts": "shorts",
    "instagram": "reels",
    "tiktok": "reels",
}
# Имена должны совпадать с зарегистрированными шаблонами в script-сервисе
# (см. Modules/script/builtin_templates.py: BUILTIN_TEMPLATES)
_PLATFORM_TEMPLATE_MAP = {
    "youtube_shorts": "shorts_story_v1",
    "instagram": "reels_hook_v1",
    "tiktok": "reels_hook_v1",
}


class CreateScriptReq(BaseModel):
    """Опциональные перекрытия для генерации аналога.

    Если ничего не передано — orchestrator сам выводит template+format из platform,
    topic — из transcript, duration — из download.duration_sec, profile — из account_id.
    """
    template: str | None = Field(default=None, description="reels_standard|shorts_hook|long")
    tone: str | None = None
    pattern_hint: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


@router.post("/runs/{run_id}/scripts", status_code=201)
async def create_run_script(run_id: str, req: CreateScriptReq | None = None):
    """Запускает script-генерацию на базе результата разбора (`run.steps.transcribe.text`).

    Условия: run.status == 'done', script_client настроен.
    Сохраняет script-meta в run.scripts[], возвращает полный script body.

    Idempotency: если у run уже есть скрипт И клиент НЕ передал явных override-ов
    (template/tone/pattern_hint/extra), возвращаем последний (без double-spend).
    Передача любых override-ов = намеренно новый вариант → создаём.
    """
    if state.script_client is None:
        raise HTTPException(503, detail="script_service_not_configured")

    run = state.run_store.get(run_id)
    if run is None:
        raise HTTPException(404, detail="run_not_found")
    if run["status"] != "done":
        raise HTTPException(409, detail=f"run_not_done: status={run['status']}")

    # Idempotency check (защита от double-click "В работу").
    # НО: возвращаем deduped только если последний script успешный — failed/error не блокируют новую попытку.
    has_overrides = req is not None and (req.template or req.tone or req.pattern_hint or req.extra)
    if not has_overrides:
        existing = run.get("scripts") or []
        if existing:
            last = existing[-1]
            last_status = (last.get("status") or "").lower()
            if last_status not in ("error", "failed", "validation_failed"):
                log.info("script_idempotent_hit", run_id=run_id, script_id=last.get("id"))
                return {**last, "deduped": True}
            log.info("script_idempotent_skipped_failed_retry", run_id=run_id,
                     prev_status=last_status, prev_id=last.get("id"))

    req = req or CreateScriptReq()

    steps = run["steps"] or {}
    transcribe = steps.get("transcribe") or {}
    download = steps.get("download") or {}

    text = transcribe.get("text") or transcribe.get("transcript_preview") or ""
    if not text:
        raise HTTPException(409, detail="transcript_unavailable")

    duration_sec = int(download.get("duration_sec") or 30)
    duration_sec = max(5, min(duration_sec, 3600))  # clamp под GenerateParams
    platform = run.get("platform") or "instagram"
    template = req.template or _PLATFORM_TEMPLATE_MAP.get(platform, "reels_standard")
    fmt = _PLATFORM_FORMAT_MAP.get(platform, "reels")

    profile_data: dict[str, Any] = {}
    account_id = run.get("account_id")
    if account_id and state.profile_client:
        try:
            full = await state.profile_client.get_full_profile(account_id)
            if full:
                profile_data = full
        except Exception as e:
            log.warning("profile_lookup_failed", run_id=run_id, error=str(e))
    # Прокидываем account_id в profile чтобы script-сервис умел поднять
    # few-shot context из feedback-store этого пользователя (этап 3).
    if account_id:
        profile_data["account_id"] = account_id

    # RAG (этап 4): если knowledge-сервис настроен и аккаунт задан —
    # подгружаем релевантные chunks по transcript-у. Best-effort: при
    # любой ошибке script всё равно будет работать без RAG.
    if account_id:
        knowledge_url = os.getenv("KNOWLEDGE_URL", "http://knowledge:8000")
        knowledge_token = os.getenv("KNOWLEDGE_TOKEN", "")
        try:
            import httpx as _httpx  # local import чтобы не нагружать import-cycle
            async with _httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(
                    f"{knowledge_url.rstrip('/')}/knowledge/query",
                    headers={"X-Worker-Token": knowledge_token},
                    json={
                        "account_id": account_id,
                        "query": text[:1500],  # transcript (первые 1500 chars)
                        "top_k": 5,
                    },
                )
            if r.status_code == 200:
                kb = r.json().get("chunks") or []
                if kb:
                    profile_data["knowledge_chunks"] = kb
                    log.info(
                        "rag_chunks_attached", run_id=run_id, count=len(kb),
                    )
        except Exception as e:  # noqa: BLE001
            log.info("rag_skipped", run_id=run_id, reason=str(e)[:120])

    params: dict[str, Any] = {
        "topic": text[:500],
        "duration_sec": duration_sec,
        "language": transcribe.get("language") or "ru",
        "format": fmt,
    }
    if req.tone:
        params["tone"] = req.tone
    if req.pattern_hint:
        params["pattern_hint"] = req.pattern_hint
    if req.extra:
        params["extra"] = req.extra

    try:
        script_resp = await state.script_client.generate(
            template=template, params=params, profile=profile_data,
        )
    except ScriptError as e:
        log.warning("script_generate_failed", run_id=run_id, error=str(e))
        raise HTTPException(502, detail=f"script_service_failed: {e}")

    # Сохраняем компактную мету (id + cost + status), сам body — на стороне script-сервиса
    meta = {
        "id": script_resp.get("id"),
        "template": script_resp.get("template") or template,
        "status": script_resp.get("status"),
        "cost_usd": script_resp.get("cost_usd"),
        "provider": script_resp.get("provider"),
        "model": script_resp.get("model"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    state.run_store.append_script(run_id, meta)

    log.info("script_created", run_id=run_id, script_id=meta["id"], template=template)
    return script_resp


@router.get("/runs/{run_id}/scripts")
async def list_run_scripts(run_id: str):
    run = state.run_store.get(run_id)
    if run is None:
        raise HTTPException(404, detail="run_not_found")
    return state.run_store.list_scripts(run_id)


@router.get("/runs/{run_id}/scripts/{script_id}")
async def get_run_script_full(run_id: str, script_id: str):
    """Прокси к script-сервису для получения full body сценария.
    В run.scripts[] хранится только мета (id, status, cost) — body живёт в script-service.
    """
    if state.script_client is None:
        raise HTTPException(503, detail="script_service_not_configured")
    run = state.run_store.get(run_id)
    if run is None:
        raise HTTPException(404, detail="run_not_found")
    # Проверяем что script_id принадлежит этому run-у
    scripts = run.get("scripts") or []
    if not any(s.get("id") == script_id for s in scripts):
        raise HTTPException(404, detail="script_not_in_run")
    # Fetch full from script-service
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(
                f"{state.script_client.base_url}/script/{script_id}",
                headers={"X-Worker-Token": state.script_client.token},
            )
        if r.status_code != 200:
            raise HTTPException(502, detail=f"script_fetch_failed: {r.status_code}")
        return r.json()
    except httpx.RequestError as e:
        raise HTTPException(502, detail=f"script_unreachable: {e}")
