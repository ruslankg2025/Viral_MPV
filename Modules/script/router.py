"""FastAPI router: /script/* — generate, get, fork, tree, export, delete."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status

from auth import require_worker_token
from constraints import validate as validate_constraints
from export import to_json, to_markdown
from generator import GenAttempt, GenContext, generate_with_retry
from schemas import (
    FeedbackReq, ForkReq, GenerateReq, ImprovePromptReq, ImprovePromptResp,
    RefineReq, ScriptBody,
)
from state import state
from viral_llm.keys.resolver import KeyResolver, NoProviderAvailable


router = APIRouter(
    prefix="/script",
    tags=["script"],
    dependencies=[Depends(require_worker_token)],
)


def _version_store():
    if state.version_store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "version_store_unavailable")
    return state.version_store


def _template_store():
    if state.template_store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "template_store_unavailable")
    return state.template_store


def _resolver() -> KeyResolver:
    if state.key_store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "key_store_unavailable")
    return KeyResolver(state.key_store)


def _resolve_template(name: str, version: str | None) -> tuple[str, str, str]:
    rec = _template_store().get(name, version=version)
    if rec is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"template_not_found: {name}:{version or 'active'}",
        )
    return rec.name, rec.version, rec.body


def _save_attempt(
    *,
    attempt: GenAttempt,
    ctx: GenContext,
    parent_id: str | None,
) -> dict[str, Any]:
    body_for_db = attempt.body.model_dump(by_alias=True) if attempt.body else {}
    return _version_store().create(
        parent_id=parent_id,
        template=ctx.template_name,
        template_version=ctx.template_version,
        schema_version=body_for_db.get("_schema_version", "1.0"),
        status=attempt.status,
        body=body_for_db,
        params=ctx.params.model_dump(),
        profile=ctx.profile,
        constraints_report=attempt.constraints_report,
        cost_usd=attempt.cost_usd,
        input_tokens=attempt.input_tokens,
        output_tokens=attempt.output_tokens,
        latency_ms=attempt.latency_ms,
        provider=attempt.provider,
        model=attempt.model,
    )


async def _run_generate(ctx: GenContext, parent_id: str | None, job_id: str) -> dict[str, Any]:
    try:
        first, retry = await generate_with_retry(
            ctx=ctx,
            resolver=_resolver(),
            job_id=job_id,
        )
    except NoProviderAvailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"no_provider: {e}")

    first_record = _save_attempt(attempt=first, ctx=ctx, parent_id=parent_id)
    if retry is None:
        return first_record

    retry_record = _save_attempt(
        attempt=retry,
        ctx=ctx,
        parent_id=first_record["id"],
    )
    return retry_record


@router.post("/generate", status_code=status.HTTP_201_CREATED)
async def generate(req: GenerateReq) -> dict[str, Any]:
    name, version, body = _resolve_template(req.template, req.template_version)
    # Few-shot context-builder (этап 3 self-learning): account_id из profile
    # передаётся в GenContext, чтобы _build_user_prompt мог подмешать
    # примеры одобренных/отклонённых скриптов этого юзера.
    account_id = (req.profile or {}).get("account_id")
    ctx = GenContext(
        template_name=name,
        template_version=version,
        template_body=body,
        params=req.params,
        profile=req.profile,
        provider=req.provider or state.settings.default_text_provider,
        account_id=account_id,
        feedback_store=state.version_store,
    )
    return await _run_generate(ctx, parent_id=None, job_id="gen")


@router.get("/{version_id}")
async def get_version(version_id: str) -> dict[str, Any]:
    rec = _version_store().get(version_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version_not_found")
    return rec


@router.get("/tree/{root_id}")
async def get_tree(root_id: str) -> list[dict[str, Any]]:
    tree = _version_store().list_tree(root_id)
    if not tree:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "root_not_found")
    return tree


@router.post("/{version_id}/fork", status_code=status.HTTP_201_CREATED)
async def fork_version(version_id: str, req: ForkReq) -> dict[str, Any]:
    base = _version_store().get(version_id)
    if base is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version_not_found")

    # Слой override: клиент может заменить любое из полей
    override = req.override or {}
    new_template = override.get("template", base["template"])
    new_template_version = override.get("template_version")  # None → active
    new_profile = override.get("profile", base["profile"])
    params_dict = dict(base["params"])
    if "params" in override and isinstance(override["params"], dict):
        params_dict.update(override["params"])
    new_provider = override.get("provider", state.settings.default_text_provider)

    # Резолвим шаблон под возможно новый template_version
    name, version, body = _resolve_template(new_template, new_template_version)

    from schemas import GenerateParams
    try:
        new_params = GenerateParams.model_validate(params_dict)
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid_params: {e}")

    ctx = GenContext(
        template_name=name,
        template_version=version,
        template_body=body,
        params=new_params,
        profile=new_profile,
        provider=new_provider,
        account_id=(new_profile or {}).get("account_id"),
        feedback_store=state.version_store,
    )
    return await _run_generate(ctx, parent_id=version_id, job_id=f"fork_{version_id[:8]}")


_REFINE_INSTRUCTIONS: dict[str, str] = {
    "amplify_hook":
        "Усиль зацепку — сделай её резче, добавь эмоциональный крючок. "
        "Все остальные части сценария можно оставить, но hook должен бить сильнее.",
    "shorten":
        "Сократи сценарий примерно на 30% сохранив ключевые сцены и CTA. "
        "Убирай слова-паразиты, дублирующие фразы, длинные перечисления.",
    "rewrite_intro":
        "Перепиши вступление (hook + первые 2 сцены) полностью. "
        "Используй другой угол подачи — другую эмоцию, другой ракурс. "
        "CTA и финал можно оставить.",
    "simplify":
        "Упрости язык — убери термины, сложные конструкции, длинные предложения. "
        "Стиль: разговорный, для людей не из этой сферы. Сохрани смысл и факты.",
}


@router.post("/{version_id}/refine", status_code=status.HTTP_201_CREATED)
async def refine_version(version_id: str, req: RefineReq) -> dict[str, Any]:
    """Рефайн = fork существующего скрипта с инжектом системной инструкции
    в pattern_hint params. LLM получает «улучшенный» промпт и возвращает
    новый сценарий. Сохраняется как child через parent_id (видно в tree).
    """
    base = _version_store().get(version_id)
    if base is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version_not_found")

    # Подбираем системную инструкцию
    if req.action == "free":
        if not req.custom_text or not req.custom_text.strip():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "custom_text_required_for_free_action",
            )
        instruction = req.custom_text.strip()
    else:
        instruction = _REFINE_INSTRUCTIONS.get(req.action, "")
        if not instruction:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"unknown_action: {req.action}"
            )

    # Готовим params: дописываем инструкцию в pattern_hint
    base_params = dict(base["params"])
    existing_hint = base_params.get("pattern_hint") or ""
    base_params["pattern_hint"] = (
        f"{existing_hint}\n[REFINE: {instruction}]" if existing_hint
        else f"[REFINE: {instruction}]"
    )

    name, version, body = _resolve_template(
        base["template"], base.get("template_version")
    )

    from schemas import GenerateParams
    try:
        new_params = GenerateParams.model_validate(base_params)
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid_params: {e}")

    ctx = GenContext(
        template_name=name,
        template_version=version,
        template_body=body,
        params=new_params,
        profile=base.get("profile") or {},
        provider=state.settings.default_text_provider,
        account_id=(base.get("profile") or {}).get("account_id"),
        feedback_store=state.version_store,
    )
    return await _run_generate(
        ctx, parent_id=version_id, job_id=f"refine_{req.action}_{version_id[:8]}",
    )


@router.delete("/{version_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_version(version_id: str) -> None:
    try:
        ok = _version_store().delete(version_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version_not_found")


@router.get("/{version_id}/export/{fmt}")
async def export_version(version_id: str, fmt: str) -> Response:
    if fmt == "docx":
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED, "docx_export_not_implemented"
        )
    if fmt not in ("markdown", "json"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"unsupported_format: {fmt}"
        )

    rec = _version_store().get(version_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version_not_found")

    body = ScriptBody.model_validate(rec["body"])
    topic = (rec.get("params") or {}).get("topic", "")

    if fmt == "markdown":
        return Response(
            content=to_markdown(body, topic=topic),
            media_type="text/markdown; charset=utf-8",
        )
    return Response(
        content=to_json(body),
        media_type="application/json; charset=utf-8",
    )


# ────────────────────────────────────────────────────────────────────
# Feedback endpoints (PLAN_SELF_LEARNING_AGENT этап 1)
# ────────────────────────────────────────────────────────────────────

@router.post("/{version_id}/feedback", status_code=status.HTTP_201_CREATED)
async def post_feedback(version_id: str, req: FeedbackReq) -> dict[str, Any]:
    """Сохранить ★/🔥/💧/комментарий на сценарий. Минимум одно поле должно
    быть задано — иначе 400. Несколько событий на один script разрешены
    (история обновлений)."""
    if (
        req.rating is None
        and req.vote is None
        and not req.comment
        and not req.refine_request
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "feedback_empty: provide rating/vote/comment/refine_request",
        )
    store = _version_store()
    if store.get(version_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version_not_found")
    try:
        fid = store.save_feedback(
            script_id=version_id,
            account_id=req.account_id,
            rating=req.rating,
            vote=req.vote,
            comment=req.comment,
            refine_request=req.refine_request,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return {"id": fid, "script_id": version_id}


@router.get("/{version_id}/feedback")
async def list_feedback(version_id: str) -> list[dict[str, Any]]:
    """История feedback-событий на конкретный сценарий (DESC по времени)."""
    store = _version_store()
    if store.get(version_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version_not_found")
    return store.list_for_script(version_id)


@router.get("/accounts/{account_id}/feedback")
async def list_account_feedback(
    account_id: str,
    days: int = 30,
    min_rating: int | None = None,
    max_rating: int | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Агрегация feedback по аккаунту — для self-learning агента."""
    store = _version_store()
    return store.list_for_account(
        account_id,
        days=max(1, min(int(days), 365)),
        min_rating=min_rating,
        max_rating=max_rating,
        limit=max(1, min(int(limit), 1000)),
    )


# ────────────────────────────────────────────────────────────────────
# Continuous self-improvement (PLAN_SELF_LEARNING_AGENT этап 5)
# ────────────────────────────────────────────────────────────────────

_IMPROVE_SYSTEM_PROMPT = (
    "Ты улучшаешь системный prompt сценариста на основе двух сигналов:\n"
    "1) **Performance роликов** (главный сигнал) — реальная реакция аудитории "
    "Instagram на уже опубликованные рилсы. Топ-квартиль по ER/velocity "
    "= что работает; нижняя квартиль = что не работает.\n"
    "2) **Оценки пользователя** ★/🔥/💧 — субъективные пометки что нравится "
    "лично создателю.\n\n"
    "**Performance важнее оценок** — если ролик получил высокий ER даже при "
    "★2 от автора, это означает что аудитория реагирует и паттерн нужно "
    "усиливать. Учитывай performance в первую очередь.\n\n"
    "Твоя задача — переписать prompt чтобы избежать паттернов из плохо "
    "работающих рилсов (низкий ER + низкие оценки) и усилить паттерны из "
    "хорошо работающих (высокий ER, даже если не нравились лично). "
    "Сохраняй структуру и формат вывода (JSON-схема ScriptBody) из текущего "
    "prompt — меняй только инструкции по стилю/содержанию.\n\n"
    "Верни ТОЛЬКО улучшенный prompt — никаких объяснений или markdown. "
    "В первой строке должно быть короткое (до 200 символов) summary что ты "
    "изменил, начинающееся с '# Изменения: ' — это будет распарсено отдельно."
)


def _format_performance_block(perf: dict[str, Any] | None) -> str:
    """Превращает performance-summary из monitor в meta-prompt блок.
    Пусто если данных нет."""
    if not perf:
        return ""
    posts = perf.get("posts") or 0
    if not posts:
        return ""
    lines = [
        f"## PERFORMANCE РОЛИКОВ ({posts} рилсов за {perf.get('days', 30)} дн)",
        f"Медиана ER: {(perf.get('median_er') or 0)*100:.1f}% · "
        f"медиана velocity: {perf.get('median_velocity') or 0}/ч",
    ]
    top = perf.get("top") or []
    bot = perf.get("bottom") or []
    if top:
        lines.append("\n### Топ-рилсы (что РАБОТАЕТ — высокий ER):")
        for r in top[:3]:
            er = (r.get("engagement_rate") or 0) * 100
            v = r.get("velocity") or 0
            desc = (r.get("description") or "").strip()[:200]
            lines.append(
                f"- ER {er:.1f}% · {v}/ч · «{desc}»" if desc
                else f"- ER {er:.1f}% · {v}/ч (без описания)"
            )
    if bot:
        lines.append("\n### Слабые рилсы (что НЕ РАБОТАЕТ — низкий ER):")
        for r in bot[:3]:
            er = (r.get("engagement_rate") or 0) * 100
            v = r.get("velocity") or 0
            desc = (r.get("description") or "").strip()[:200]
            lines.append(
                f"- ER {er:.1f}% · {v}/ч · «{desc}»" if desc
                else f"- ER {er:.1f}% · {v}/ч (без описания)"
            )
    return "\n".join(lines)


def _format_examples_for_meta(items: list[dict[str, Any]], label: str) -> str:
    if not items:
        return ""
    lines = [f"# {label}:"]
    for item in items:
        body = item.get("body") or {}
        hook = ((body.get("hook") or {}).get("text") or "").strip()
        if not hook:
            continue
        rating = item.get("rating")
        comment = (item.get("comment") or "").strip()
        line = f'- ★{rating}: «{hook[:200]}»'
        if comment:
            line += f' (комментарий: "{comment[:200]}")'
        lines.append(line)
    return "\n".join(lines) if len(lines) > 1 else ""


@router.post("/improve-prompt", response_model=ImprovePromptResp)
async def improve_prompt(req: ImprovePromptReq) -> ImprovePromptResp:
    """Анализирует feedback аккаунта за N дней, через LLM генерирует
    улучшенную версию системного промпта. Сам не пишет в profile-сервис —
    только возвращает suggestion (orchestration на стороне shell-сервиса
    или ручная активация через UI).

    Возвращает:
      status='not_enough_data' если событий < min_events
      status='no_pattern' если нет одновременно loved (≥4) и hated (≤2)
      status='improved' с suggested_prompt — иначе
    """
    store = _version_store()
    feedback = store.list_for_account(
        req.account_id, days=req.days, limit=200,
    )
    n = len(feedback)
    loved = [f for f in feedback if (f.get("rating") or 0) >= 4]
    hated = [f for f in feedback if 0 < (f.get("rating") or 0) <= 2]

    # Performance — главный сигнал. Если есть perf с posts ≥ 3 — этого
    # достаточно для улучшения, даже если feedback мало.
    perf = req.performance or {}
    perf_posts = perf.get("posts") or 0
    perf_has_data = perf_posts >= 3 and (perf.get("top") or perf.get("bottom"))

    # «not_enough_data» — нужно либо feedback, либо performance
    if n < req.min_events and not perf_has_data:
        return ImprovePromptResp(
            status="not_enough_data",
            feedback_count=n,
            loved_count=len(loved),
            hated_count=len(hated),
            performance_used=False,
        )
    # «no_pattern» — нет performance И нет одновременно loved+hated
    if not perf_has_data and (not loved or not hated):
        return ImprovePromptResp(
            status="no_pattern",
            feedback_count=n,
            loved_count=len(loved),
            hated_count=len(hated),
            performance_used=False,
        )

    # Подгружаем body для топ-3 loved и топ-2 hated (через storage helpers)
    loved_examples = store.top_rated_for_account(req.account_id, limit=3, min_rating=4)
    hated_examples = store.bottom_rated_for_account(req.account_id, limit=2, max_rating=2)

    perf_block = _format_performance_block(perf if perf_has_data else None)

    user_msg_parts = [
        "## Текущий system prompt:",
        req.current_prompt,
        "",
        # Performance первый — главный сигнал
        perf_block,
        "",
        _format_examples_for_meta(loved_examples, "Сценарии что ПОНРАВИЛИСЬ автору ★≥4"),
        _format_examples_for_meta(hated_examples, "Сценарии что НЕ ПОНРАВИЛИСЬ автору ★≤2"),
        "",
        "Напиши улучшенный prompt согласно инструкциям системы. "
        "Помни: performance аудитории важнее личных оценок автора.",
    ]
    user_msg = "\n".join(p for p in user_msg_parts if p)

    # Используем resolver как везде — provider 'auto' (default)
    resolver = _resolver()

    async def _call(key_record: dict[str, Any], secret: str):
        from viral_llm.clients.registry import get_text_client
        from viral_llm.keys.pricing import estimate_cost
        from viral_llm.keys.resolver import UsageResult
        client = get_text_client(key_record["provider"])
        gr = await client.generate(
            system=_IMPROVE_SYSTEM_PROMPT,
            user=user_msg,
            api_key=secret,
        )
        cost = estimate_cost(
            gr.provider, gr.model,
            input_tokens=gr.input_tokens, output_tokens=gr.output_tokens,
        )
        return UsageResult(
            result=gr, provider=gr.provider, model=gr.model,
            cost_usd=cost,
            input_tokens=gr.input_tokens, output_tokens=gr.output_tokens,
            latency_ms=gr.latency_ms,
        )

    try:
        usage = await resolver.run_with_fallback(
            kind="vision",
            job_id=f"improve_{req.account_id[:8]}",
            operation="improve_prompt",
            provider=None,
            call=_call,
        )
    except NoProviderAvailable as e:
        raise HTTPException(503, detail=f"no_provider: {e}")

    text = (usage.result.text or "").strip()
    rationale = None
    if text.startswith("# Изменения:"):
        first_line, _, rest = text.partition("\n")
        rationale = first_line.removeprefix("# Изменения:").strip()
        text = rest.lstrip()

    return ImprovePromptResp(
        status="improved",
        suggested_prompt=text,
        feedback_count=n,
        loved_count=len(loved),
        hated_count=len(hated),
        performance_used=perf_has_data,
        cost_usd=usage.cost_usd,
        rationale=rationale,
    )
