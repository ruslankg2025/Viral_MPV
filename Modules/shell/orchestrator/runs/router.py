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


class ManualScriptReq(BaseModel):
    """Создать сценарий напрямую из текста/идеи (без download/transcribe).

    Хотя бы одно из (title, text, idea) должно быть задано. Создаёт
    синтетический run со status='done' и transcribe.text=собранный
    input — дальше пользователь жмёт «В работу» (или сразу
    auto_generate=true) и script-сервис генерит сценарий.
    """
    title: str | None = Field(default=None, max_length=300)
    text: str | None = Field(default=None, max_length=10000)
    idea: str | None = Field(default=None, max_length=2000)
    account_id: str | None = None
    duration_sec: int | None = Field(default=30, ge=5, le=600)
    script_template: str | None = None
    auto_generate: bool = True  # сразу запустить script-генерацию


@router.post("/runs/manual", status_code=201)
async def create_manual_run(req: ManualScriptReq):
    """Создать manual-run из текста/заголовка/идеи и (опц.) сразу
    сгенерировать сценарий. Run появляется в Сценарии-табе как обычный."""
    parts = [
        ("# " + req.title.strip()) if req.title else None,
        req.text.strip() if req.text else None,
        ("Идея: " + req.idea.strip()) if req.idea else None,
    ]
    topic = "\n\n".join(p for p in parts if p)
    if not topic:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="manual_script_empty: provide at least one of title/text/idea",
        )

    import uuid as _uuid
    run_id = state.run_store.create(
        url=f"manual://{_uuid.uuid4().hex[:8]}",
        platform="manual",
        external_id=None,
        account_id=req.account_id,
        script_template=req.script_template,
    )
    # Метаданные «как у обычного run»: title для отображения в карточке
    state.run_store.set_video_meta(run_id, {
        "manual": True,
        "title": (req.title or req.idea or req.text or "")[:80].strip() or "Ручной сценарий",
    })
    # Кладём synthetic transcribe.text + download (нужно чтобы create_run_script прошёл)
    state.run_store.patch_step(run_id, "transcribe", {
        "text": topic,
        "language": "ru",
    })
    state.run_store.patch_step(run_id, "download", {
        "duration_sec": req.duration_sec or 30,
    })
    state.run_store.set_status(run_id, "done")
    log.info("manual_run_created", run_id=run_id, account_id=req.account_id)

    if not req.auto_generate:
        return {"run_id": run_id, "status": "done", "script_generated": False}

    # Сразу запускаем script-генерацию (используем существующую логику)
    try:
        script_resp = await create_run_script(run_id, CreateScriptReq(
            template=req.script_template,
        ))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, detail=f"script_generation_failed: {e}")

    return {
        "run_id": run_id,
        "status": "done",
        "script_generated": True,
        "script_id": script_resp.get("id") if isinstance(script_resp, dict) else None,
    }


class PublishedReelReq(BaseModel):
    """Добавить ссылку на уже-опубликованный рилс — для изучения паттернов
    своих публикаций (даже если разбор/сценарий не делались).

    URL парсится — определяется platform/external_id. Создаётся run с
    platform='instagram'|'tiktok'|'youtube_shorts' и маркером
    is_published=True в video_meta.

    Если auto_analyze=True (default) — запускается обычный pipeline
    (download → transcribe → vision → strategy) и Размещённые получают
    полный разбор тех же роликов что и обычный Разбор.
    """
    url: HttpUrl
    title: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=1000)
    account_id: str | None = None
    auto_analyze: bool = False  # default off — иначе тратим Apify-кредиты


def _parse_url_platform(url_str: str) -> tuple[str, str | None]:
    """Грубое определение platform + external_id из URL."""
    s = url_str.lower()
    if "instagram.com" in s:
        # /reel/{id}/ или /p/{id}/
        import re as _re
        m = _re.search(r"/(?:reel|p)/([a-zA-Z0-9_-]+)", url_str)
        return "instagram", (m.group(1) if m else None)
    if "tiktok.com" in s:
        m = __import__("re").search(r"/video/(\d+)", url_str)
        return "tiktok", (m.group(1) if m else None)
    if "youtube.com" in s or "youtu.be" in s:
        import re as _re
        m = _re.search(r"(?:shorts/|v=|youtu\.be/)([a-zA-Z0-9_-]{8,})", url_str)
        return "youtube_shorts", (m.group(1) if m else None)
    return "unknown", None


@router.post("/published", status_code=201)
async def add_published_reel(req: PublishedReelReq):
    """Добавить ссылку на опубликованный рилс в Размещённые-таб."""
    url_str = str(req.url)
    platform, external_id = _parse_url_platform(url_str)
    if platform == "unknown":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported_url: {url_str}",
        )

    # Дедуп: если URL уже есть как published — возвращаем его
    existing = find_active_duplicate(state.run_store, video_id=None, url=url_str)
    if existing:
        return {
            "run_id": existing["id"],
            "deduped": True,
            "auto_analyze_started": False,
        }

    run_id = state.run_store.create(
        url=url_str,
        platform=platform,
        external_id=external_id,
        account_id=req.account_id,
    )
    state.run_store.set_video_meta(run_id, {
        "is_published": True,
        "title": (req.title or url_str)[:80],
        "note": req.note,
    })

    if req.auto_analyze:
        state.runner.kick_off(run_id)
        log.info("published_reel_added_with_analyze", run_id=run_id, url=url_str)
        return {
            "run_id": run_id, "deduped": False,
            "auto_analyze_started": True, "status": "queued",
        }
    # Без анализа — просто фиксация ссылки
    state.run_store.set_status(run_id, "done")
    log.info("published_reel_registered", run_id=run_id, url=url_str)
    return {
        "run_id": run_id, "deduped": False,
        "auto_analyze_started": False, "status": "done",
    }


@router.get("/published")
async def list_published_reels(
    account_id: str | None = None, limit: int = 100,
) -> list[dict[str, Any]]:
    """Список published-рилсов: фильтр video_meta.is_published == True."""
    rows = state.run_store.list_recent(limit=max(1, min(limit, 500)))
    out = []
    for r in rows:
        meta = r.get("video_meta") or {}
        if not meta.get("is_published"):
            continue
        if account_id and r.get("account_id") != account_id:
            continue
        out.append(r)
    return out


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
