"""Script generator core: template → LLM → validate → (retry one time) → store."""
import json
import re
from dataclasses import dataclass
from typing import Any

from builtin_templates import BUILTIN_TEMPLATES
from constraints import validate
from schemas import (
    GenerateParams,
    ScriptBody,
    SCRIPT_SCHEMA_VERSION,
)
from viral_llm.clients.base import GenerationResult, ProviderError, TextGenerationClient
from viral_llm.keys.pricing import estimate_cost
from viral_llm.keys.resolver import KeyResolver, UsageResult

SYSTEM_RETRY_ADDENDUM = (
    "\n\n⚠ Previous attempt failed validation: {reasons}. "
    "Fix this and return corrected JSON only."
)


class GenerationFailed(RuntimeError):
    pass


@dataclass
class GenContext:
    template_name: str
    template_version: str
    template_body: str
    params: GenerateParams
    profile: dict[str, Any]
    provider: str | None  # None → resolver выберет по приоритету


@dataclass
class GenAttempt:
    """Результат одной попытки генерации (успешной или failed-constraints)."""
    status: str                        # "ok" | "validation_failed" | "error"
    body: ScriptBody | None
    raw_text: str
    constraints_report: dict[str, Any] | None
    cost_usd: float
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    provider: str
    model: str


def _extract_json(text: str) -> dict:
    """То же, что в viral_llm.clients.anthropic_claude — парсер JSON из текста."""
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    raise ProviderError(f"cannot_parse_script_json: {text[:200]}")


def _build_user_prompt(ctx: GenContext) -> str:
    parts = [
        f"Topic: {ctx.params.topic}",
        f"Target duration: {ctx.params.duration_sec}s",
        f"Format: {ctx.params.format}",
        f"Language: {ctx.params.language}",
    ]
    if ctx.params.tone:
        parts.append(f"Tone: {ctx.params.tone}")
    if ctx.params.pattern_hint:
        parts.append(f"Pattern hint: {ctx.params.pattern_hint}")
    if ctx.profile:
        parts.append(f"Profile: {json.dumps(ctx.profile, ensure_ascii=False)}")
    if ctx.params.extra:
        parts.append(f"Extra: {json.dumps(ctx.params.extra, ensure_ascii=False)}")
    return "\n".join(parts)


def _fill_template(body: str, ctx: GenContext) -> str:
    """Минимальная подстановка плейсхолдеров {language} в теле шаблона."""
    try:
        return body.format(language=ctx.params.language)
    except Exception:
        return body


async def _run_one_attempt(
    *,
    ctx: GenContext,
    system_addendum: str,
    resolver: KeyResolver,
    job_id: str,
) -> GenAttempt:
    system_prompt = _fill_template(ctx.template_body, ctx) + system_addendum
    user_prompt = _build_user_prompt(ctx)

    async def _call(key_record: dict[str, Any], secret: str) -> UsageResult:
        from viral_llm.clients.registry import get_text_client
        client: TextGenerationClient = get_text_client(key_record["provider"])
        gr: GenerationResult = await client.generate(
            system=system_prompt,
            user=user_prompt,
            api_key=secret,
        )
        cost = estimate_cost(
            gr.provider,
            gr.model,
            input_tokens=gr.input_tokens,
            output_tokens=gr.output_tokens,
        )
        return UsageResult(
            result=gr,
            provider=gr.provider,
            model=gr.model,
            cost_usd=cost,
            input_tokens=gr.input_tokens,
            output_tokens=gr.output_tokens,
            latency_ms=gr.latency_ms,
        )

    usage = await resolver.run_with_fallback(
        kind="vision",  # text clients зарегистрированы под kind=vision (см. plan §2.2)
        job_id=job_id,
        operation="script_generate",
        provider=ctx.provider if ctx.provider not in (None, "auto") else None,
        call=_call,
    )

    gr: GenerationResult = usage.result
    raw_text = gr.text

    # Парсим JSON и валидируем через pydantic ScriptBody
    try:
        parsed = _extract_json(raw_text)
    except ProviderError as e:
        return GenAttempt(
            status="error",
            body=None,
            raw_text=raw_text,
            constraints_report={"passed": False, "violations": [
                {"code": "json_parse_failed", "severity": "hard", "message": str(e)[:200]}
            ]},
            cost_usd=usage.cost_usd,
            input_tokens=gr.input_tokens,
            output_tokens=gr.output_tokens,
            latency_ms=gr.latency_ms,
            provider=gr.provider,
            model=gr.model,
        )

    # Гарантируем наличие meta с корректными полями шаблона/длительности
    parsed.setdefault("meta", {})
    parsed["meta"]["template"] = ctx.template_name
    parsed["meta"]["template_version"] = ctx.template_version
    parsed["meta"].setdefault("language", ctx.params.language)
    parsed["meta"].setdefault("target_duration_sec", ctx.params.duration_sec)
    parsed["meta"].setdefault("format", ctx.params.format)
    parsed.setdefault("_schema_version", SCRIPT_SCHEMA_VERSION)

    try:
        body_obj = ScriptBody.model_validate(parsed)
    except Exception as e:
        return GenAttempt(
            status="error",
            body=None,
            raw_text=raw_text,
            constraints_report={"passed": False, "violations": [
                {"code": "schema_invalid", "severity": "hard", "message": str(e)[:200]}
            ]},
            cost_usd=usage.cost_usd,
            input_tokens=gr.input_tokens,
            output_tokens=gr.output_tokens,
            latency_ms=gr.latency_ms,
            provider=gr.provider,
            model=gr.model,
        )

    report = validate(body_obj, ctx.params)
    status = "ok" if report.passed else "validation_failed"

    return GenAttempt(
        status=status,
        body=body_obj,
        raw_text=raw_text,
        constraints_report=report.model_dump(),
        cost_usd=usage.cost_usd,
        input_tokens=gr.input_tokens,
        output_tokens=gr.output_tokens,
        latency_ms=gr.latency_ms,
        provider=gr.provider,
        model=gr.model,
    )


async def generate_with_retry(
    *,
    ctx: GenContext,
    resolver: KeyResolver,
    job_id: str,
) -> tuple[GenAttempt, GenAttempt | None]:
    """Возвращает (first_attempt, retry_attempt_or_None).

    Если первая попытка прошла (status=ok) — retry не делается.
    Если первая failed (validation_failed | error) — делается ровно один retry
    с system_addendum, описывающим причину.
    """
    first = await _run_one_attempt(
        ctx=ctx,
        system_addendum="",
        resolver=resolver,
        job_id=job_id,
    )
    if first.status == "ok":
        return first, None

    reasons = "unknown"
    if first.constraints_report:
        violations = first.constraints_report.get("violations") or []
        reasons = "; ".join(
            f"{v.get('code')}: {v.get('message')}"
            for v in violations
            if v.get("severity") == "hard"
        ) or "schema_or_json_error"

    addendum = SYSTEM_RETRY_ADDENDUM.format(reasons=reasons[:500])
    retry = await _run_one_attempt(
        ctx=ctx,
        system_addendum=addendum,
        resolver=resolver,
        job_id=f"{job_id}_retry",
    )
    return first, retry
