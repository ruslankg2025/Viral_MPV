"""Strategy/virality analysis task — генерирует 5-секционный JSON разбор виральности.

На вход: transcript text + vision raw_json.
На выход: {sections: [{id, title, body}]} — 5 разделов:
  why, audience, triggers, windows, recipe

Использует text-generation LLM (default: Claude Sonnet 4.6) через KeyResolver
для биллинга через тот же ledger что vision/transcribe.
"""
import json
import re
from typing import Any

from cache.store import build_cache_key
from logging_setup import get_logger
from viral_llm.clients.registry import get_text_client
from viral_llm.keys.pricing import estimate_cost
from viral_llm.keys.resolver import KeyResolver, UsageResult
from state import state

log = get_logger("tasks.analyze_strategy")

_SYSTEM_PROMPT = """Ты — старший продюсер вирусного short-form видео контента.
Твоя задача — проанализировать готовый разбор рилса (транскрипт + покадровый анализ)
и выдать стратегический разбор для русскоязычного автора, который хочет адаптировать формат.

Возврати СТРОГО валидный JSON в формате:
{
  "sections": [
    {"id": "why",       "title": "Почему этот ролик залетел", "body": "..."},
    {"id": "audience",  "title": "Целевая аудитория",         "body": "..."},
    {"id": "triggers",  "title": "Эмоциональные триггеры",    "body": "..."},
    {"id": "windows",   "title": "Окна публикации",           "body": "..."},
    {"id": "recipe",    "title": "Что делать тебе",           "body": "..."}
  ]
}

Требования к секциям (КРАТКО, плотно):
- "why" — что цепляет в первые 1-3 сек + ключевая структурная особенность
- "audience" — демография + 1 главная боль/триггер ЦА
- "triggers" — последовательность эмоций по таймлайну
- "windows" — лучшее время постинга с коротким обоснованием
- "recipe" — практический рецепт адаптации, 3-4 пункта

ЖЁСТКИЕ ЛИМИТЫ:
- Каждый body — 2-3 коротких предложения, **40-70 слов** максимум
- Без markdown, без bullet-points — сплошной параграф
- Без вступлений «Этот ролик...», «В данном видео...» — сразу суть
- Без воды, без общих фраз. Только конкретика.
"""


def _build_user_prompt(transcript_text: str, vision_block: dict[str, Any]) -> str:
    # Срезаем frames-list внутри vision_block, чтобы не раздувать промпт
    vision_lite = dict(vision_block)
    if isinstance(vision_lite, dict):
        # Оставляем только ключи с описаниями сцен/высокоуровневыми инсайтами
        vision_lite.pop("frames_metadata", None)

    parts = [
        "ТРАНСКРИПТ ВИДЕО:",
        transcript_text.strip()[:4000] if transcript_text else "(транскрипт пустой)",
        "",
        "ПОКАДРОВЫЙ АНАЛИЗ (vision LLM output):",
        json.dumps(vision_lite, ensure_ascii=False)[:6000] if vision_lite else "(анализ кадров пустой)",
        "",
        "Сгенерируй JSON-разбор (5 секций) согласно system-prompt.",
    ]
    return "\n".join(parts)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Извлечь top-level JSON-объект из ответа LLM с устойчивостью к:
    - markdown-обёртке ```json ... ```
    - prefix/suffix-тексту вокруг JSON
    - вложенным {} в строках (правильный подсчёт скобок)
    """
    if not text:
        return None
    # 1. Стрипаем markdown-обёртку
    cleaned = text.strip()
    fence = re.match(r"^```(?:json|JSON)?\s*\n?(.*?)\n?```\s*$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    else:
        # Может быть только открывающий fence без закрывающего
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json|JSON)?\s*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```\s*$", "", cleaned)

    # 2. Пробуем парсить как есть
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 3. Поиск top-level {...} через подсчёт скобок (учитываем строки и escape)
    start = cleaned.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                # нашли парный закрывающий
                snippet = cleaned[start:i+1]
                try:
                    return json.loads(snippet)
                except json.JSONDecodeError:
                    return None
    # JSON не закрылся — возможно truncate. Пытаемся достроить.
    if depth > 0:
        snippet = cleaned[start:] + ("}" * depth)
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            return None
    return None


def _validate_sections(parsed: dict[str, Any]) -> list[dict[str, str]]:
    """Возвращает 5 валидированных секций или raise ValueError.

    Пять секций обязательны: why/audience/triggers/windows/recipe.
    Если LLM вернул больше — берём первые 5 по порядку.
    """
    sections = parsed.get("sections") if isinstance(parsed, dict) else None
    if not isinstance(sections, list) or not sections:
        raise ValueError("missing_or_empty_sections")
    expected_ids = {"why", "audience", "triggers", "windows", "recipe"}
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for s in sections:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id", "")).strip().lower()
        title = str(s.get("title", "")).strip()
        body = str(s.get("body", "")).strip()
        if not sid or not title or not body:
            continue
        if sid in seen:
            continue
        seen.add(sid)
        out.append({"id": sid, "title": title, "body": body})
        if len(out) == 5:
            break
    if len(out) < 5:
        missing = expected_ids - {s["id"] for s in out}
        raise ValueError(f"incomplete_sections: missing={sorted(missing)} got={[s['id'] for s in out]}")
    return out


async def run_analyze_strategy(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    transcript_text: str = payload.get("transcript_text") or ""
    vision_block: dict[str, Any] = payload.get("vision_analysis") or {}
    cache_key_base = payload.get("cache_key") or None
    source_ref = payload.get("source_ref") or None
    provider = payload.get("provider") or None

    if not transcript_text and not vision_block:
        raise RuntimeError("no_input_data: both transcript and vision empty")

    cache_key = build_cache_key(
        cache_key_base, prompt_version="strategy_v1", provider=provider,
    )

    if cache_key:
        cached = state.cache_store.get(cache_key, "strategy")
        if cached:
            log.info("strategy_cache_hit", job_id=job_id, cache_key=cache_key)
            return {**cached, "from_cache": True}

    user_prompt = _build_user_prompt(transcript_text, vision_block)

    resolver = KeyResolver(state.key_store)

    async def _call(key_record: dict[str, Any], secret: str) -> UsageResult:
        client = get_text_client(key_record["provider"])
        gr = await client.generate(
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            api_key=secret,
            max_tokens=1800,  # короткая стратегия (40-70 слов × 5) ≈ 1500 tokens output
        )
        cost = estimate_cost(
            gr.provider, gr.model,
            input_tokens=gr.input_tokens, output_tokens=gr.output_tokens,
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

    # text-providers зарегистрированы в TEXT_GENERATION_CLIENTS как vision-kind
    # для целей биллинга (см. pricing.py: anthropic_claude_text имеет kind="vision")
    usage = await resolver.run_with_fallback(
        kind="vision",  # text-gen использует ту же таксономию ключей
        job_id=job_id,
        operation="analyze_strategy",
        provider=provider if provider not in (None, "auto") else None,
        call=_call,
    )

    gr = usage.result
    parsed = _extract_json(gr.text)
    if parsed is None:
        raise RuntimeError(f"invalid_json_response: {gr.text[:200]}")

    sections = _validate_sections(parsed)

    result: dict[str, Any] = {
        "sections": sections,
        "provider": gr.provider,
        "model": gr.model,
        "input_tokens": gr.input_tokens,
        "output_tokens": gr.output_tokens,
        "latency_ms": gr.latency_ms,
        "cost_usd": {"strategy": round(usage.cost_usd, 6)},
        "prompt_version": "strategy_v1",
    }
    if source_ref:
        result["source_ref"] = source_ref

    if cache_key:
        state.cache_store.set(cache_key, "strategy", result)

    return result
