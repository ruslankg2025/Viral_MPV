"""Адаптация текста под карусель (Заголовок + Текст → 7 слайдов).

LLM-режим (через viral_llm) — умная разбивка + сокращение + усиление, и refine
(«Усилить / Сделать короче»). При отсутствии активного ключа или ошибке —
graceful fallback на детерминированную разбивку (работает локально без ключей).
"""
from __future__ import annotations

import json
import re
import uuid

from logging_setup import get_logger

log = get_logger()

_SENT_SPLIT = re.compile(r"(?<=[.!?…»])\s+")
_ENUM = re.compile(r"^\s*\d+[.)]\s*")


# ─── детерминированный fallback ───────────────────────────────────────────────
def _paragraphs(text: str) -> list[str]:
    out = []
    for p in re.split(r"\n+", text or ""):
        p = _ENUM.sub("", p.strip())
        if p:
            out.append(p)
    return out


def _sentences(p: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(p.strip()) if s.strip()]


def _short(p: str, max_words: int = 7) -> str:
    words = p.split()
    return " ".join(words[:max_words]).rstrip(".,;:—-") if len(words) > max_words else p


def split_fallback(title: str, text: str) -> list[dict]:
    paras = _paragraphs(text)

    cta_para = None
    if paras and paras[-1].rstrip().endswith("?"):
        cta_para = paras.pop()

    if len(paras) > 5:
        paras = paras[:4] + [" ".join(paras[4:])]
    points = paras[:5]

    lines = [ln.strip() for ln in (title or "").splitlines() if ln.strip()]
    main = lines[0] if lines else (title or "").strip()
    sub = " ".join(lines[1:]) if len(lines) > 1 else ""

    slides: list[dict] = [{"idx": 1, "role": "hook", "heading": main, "body": sub}]
    for i, p in enumerate(points, start=2):
        sents = _sentences(p)
        if len(sents) > 1:
            heading, body = sents[0], " ".join(sents[1:])
        else:
            heading, body = _short(p), p
        slides.append({"idx": i, "role": "point", "heading": heading, "body": body})
    while len(slides) < 6:
        slides.append({"idx": len(slides) + 1, "role": "point", "heading": "", "body": ""})

    lead = cta_para or "Сохрани пост, если откликнулось — и напиши в комментариях своё мнение."
    slides.append({"idx": 7, "role": "cta", "heading": lead, "body": ""})
    return slides[:7]


def refine_fallback(heading: str, body: str, action: str) -> tuple[str, str]:
    if action == "shorten":
        sents = _sentences(body)
        if len(sents) > 1:
            body = " ".join(sents[: max(1, len(sents) - 1)])
    return heading, body


# ─── LLM-режим ────────────────────────────────────────────────────────────────
# ── уровни виральности (интрига) и сжатия ────────────────────────────────────
_INTRIGUE = {
    "off": "",
    "mid": "Недосказанность: слайды 2–6 заканчивай так, чтобы хотелось листать дальше — не раскрывай вывод полностью на одном слайде.",
    "max": ("Недосказанность (КЛЮЧЕВОЕ): каждый слайд 2–6 обрывай на самом интересном — открытая петля/клиффхэнгер, "
            "как в сериале. Не раскрывай инсайт сразу: дай интригу и намёк, что развязка дальше. Полное раскрытие — "
            "в финале и CTA. Каждый слайд должен вызывать «а что дальше?» и провоцировать листать."),
}
_LIMITS = {  # символьные лимиты по степени сжатия
    "light":  {"hh": 110, "hb": 110, "ph": 70, "pb": 380, "ch": 240, "cb": 220},
    "mid":    {"hh": 90,  "hb": 80,  "ph": 60, "pb": 300, "ch": 200, "cb": 180},
    "strong": {"hh": 75,  "hb": 60,  "ph": 48, "pb": 200, "ch": 160, "cb": 140},
}
_COMPRESS_HINT = {
    "light":  "Сокращай умеренно — сохраняй детали и живость оригинала.",
    "mid":    "Сокращай и усиливай смысл, убирай воду.",
    "strong": "Сжимай агрессивно — только суть и самые сильные фразы, минимум слов.",
}


def _build_adapt_system(intrigue: str = "mid", compression: str = "mid") -> str:
    lim = _LIMITS.get(compression, _LIMITS["mid"])
    intr = _INTRIGUE.get(intrigue, _INTRIGUE["mid"])
    intr_line = f"\n- {intr}" if intr else ""
    return f"""Ты — редактор виральных Instagram-каруселей. Преврати присланные Заголовок и Текст (сценарий рилса) в карусель РОВНО из 7 слайдов.

Структура:
- Слайд 1 (role=hook): heading — цепляющий заголовок-хук; body — короткий подзаголовок-обещание.
- Слайды 2–6 (role=point): heading — короткий заголовок-принцип/инсайт (3–7 слов, БЕЗ номеров); body — живой абзац 2–4 предложения.
- Слайд 7 (role=cta): heading — сильный финальный вопрос или призыв; body — короткий оффер (призыв написать слово в комментариях / сохранить).

Тон и стиль:
- Сохраняй эмоцию и разговорный стиль оригинала, КАПС в цитатах крика (например «СКОЛЬКО МОЖНО?!») и маскировку (стр*шное).
- Экспертность передавай структурой и заголовками-принципами, а не сухим канцеляритом.
- {_COMPRESS_HINT.get(compression, _COMPRESS_HINT['mid'])} Язык — русский.{intr_line}

Жёсткие лимиты (текст ОБЯЗАН влезать в слайд):
- hook: heading ≤ {lim['hh']} симв, body ≤ {lim['hb']}.
- point: heading ≤ {lim['ph']}, body ≤ {lim['pb']}.
- cta: heading ≤ {lim['ch']}, body ≤ {lim['cb']}.

Верни СТРОГО JSON без markdown и пояснений:
{{"slides":[{{"idx":1,"role":"hook","heading":"...","body":"..."}},{{"idx":2,"role":"point","heading":"...","body":"..."}},{{"idx":3,"role":"point","heading":"...","body":"..."}},{{"idx":4,"role":"point","heading":"...","body":"..."}},{{"idx":5,"role":"point","heading":"...","body":"..."}},{{"idx":6,"role":"point","heading":"...","body":"..."}},{{"idx":7,"role":"cta","heading":"...","body":"..."}}]}}"""

_JSON_SHAPE = (
    '\n\nВерни СТРОГО JSON без markdown и пояснений:\n'
    '{"slides":[{"idx":1,"role":"hook","heading":"...","body":"..."},'
    '{"idx":2,"role":"point","heading":"...","body":"..."},'
    '{"idx":3,"role":"point","heading":"...","body":"..."},'
    '{"idx":4,"role":"point","heading":"...","body":"..."},'
    '{"idx":5,"role":"point","heading":"...","body":"..."},'
    '{"idx":6,"role":"point","heading":"...","body":"..."},'
    '{"idx":7,"role":"cta","heading":"...","body":"..."}]}'
)


def _build_gentle_system(intrigue: str = "mid", compression: str = "mid") -> str:
    """Бережный режим: делит на слайды, сохраняя формулировки автора; не переписывает."""
    lim = _LIMITS.get(compression, _LIMITS["mid"])
    intr = _INTRIGUE.get(intrigue, _INTRIGUE["mid"])
    intr_line = f"\n- {intr} — но ТОЛЬКО выбором места разбивки, не переписыванием." if intr else ""
    return (
        "Ты — редактор Instagram-каруселей. Разложи присланные Заголовок и Текст на карусель "
        "РОВНО из 7 слайдов, МАКСИМАЛЬНО сохраняя формулировки автора.\n\n"
        "ГЛАВНОЕ: НЕ переписывай и НЕ перефразируй. Бери фразы автора дословно. Разрешено только:\n"
        "- выбрать, где разбить текст на слайды;\n"
        "- убрать явный балласт (повторы, слова-паразиты), если не влезает по лимиту;\n"
        "- в заголовок пункта вынести короткую фразу ИЗ авторского текста.\n"
        "Слова и смысл автора должны сохраниться. КАПС и маскировку (стр*шное) не трогай.\n\n"
        "Структура: слайд 1 = hook (Заголовок), слайды 2–6 = point, слайд 7 = cta.\n"
        f"Лимиты (при переполнении — убирай лишнее, НЕ переписывай): point body ≤ {lim['pb']}, "
        f"heading ≤ {lim['ph']}, hook heading ≤ {lim['hh']}.{intr_line}"
        + _JSON_SHAPE
    )


_ACTION_DESC = {
    "strengthen": "усилить смысл и эмоцию, сделать формулировку ярче и убедительнее",
    "shorten": "сократить, убрать воду, сохранив суть и стиль",
    "expand": "немного развернуть мысль, добавить конкретики",
}


def _extract_json(text: str):
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            return json.loads(m.group(0))
        raise ValueError("no_json_in_response")


async def _llm_generate(system: str, user: str, provider: str | None, *, max_tokens: int = 2048) -> str:
    from state import state
    from viral_llm.clients.registry import get_text_client
    from viral_llm.keys.pricing import estimate_cost
    from viral_llm.keys.resolver import KeyResolver, UsageResult

    resolver = KeyResolver(state.key_store)

    async def _call(key_record, secret) -> UsageResult:
        client = get_text_client(key_record["provider"])
        gr = await client.generate(system=system, user=user, api_key=secret, max_tokens=max_tokens)
        cost = estimate_cost(gr.provider, gr.model, input_tokens=gr.input_tokens, output_tokens=gr.output_tokens)
        return UsageResult(
            result=gr, provider=gr.provider, model=gr.model, cost_usd=cost,
            input_tokens=gr.input_tokens, output_tokens=gr.output_tokens, latency_ms=gr.latency_ms,
        )

    usage = await resolver.run_with_fallback(
        kind="vision",  # text-клиенты зарегистрированы под kind=vision (как в script)
        job_id=uuid.uuid4().hex,
        operation="carousel_textfit",
        provider=provider,
        call=_call,
    )
    return usage.result.text


def _coerce_slides(data) -> list[dict]:
    slides = data.get("slides") if isinstance(data, dict) else data
    if not isinstance(slides, list) or len(slides) != 7:
        raise ValueError(f"expected_7_slides_got_{len(slides) if isinstance(slides, list) else 'none'}")
    roles = ["hook"] + ["point"] * 5 + ["cta"]
    out = []
    for i, (sl, role) in enumerate(zip(slides, roles), start=1):
        out.append({
            "idx": i,
            "role": sl.get("role", role) or role,
            "heading": (sl.get("heading") or "").strip(),
            "body": (sl.get("body") or "").strip(),
        })
    return out


def _provider_chain(preferred: str | None = None) -> list[str]:
    """Упорядоченный список text-провайдеров с активными ключами (первый — приоритетный).
    Пусто → LLM недоступен (нет ключей / fake_llm). Цепочка даёт fallback между
    провайдерами: напр. если у OpenAI баланс кончился — пробуем Anthropic.
    """
    from config import get_settings
    from state import state
    s = get_settings()
    if s.carousel_fake_llm or not state.key_store:
        return []
    order: list[str] = []
    for p in (preferred, s.default_text_provider, "openai_gpt4o_text", "anthropic_claude_text"):
        if p and p not in order:
            order.append(p)
    out: list[str] = []
    for p in order:
        try:
            if state.key_store.list_active("vision", provider=p):
                out.append(p)
        except Exception:
            pass
    return out


# ─── публичный API ────────────────────────────────────────────────────────────
async def adapt(
    title: str, text: str, *, provider: str | None = None,
    intrigue: str = "mid", compression: str = "mid", text_mode: str = "ai",
) -> list[dict]:
    # verbatim — детерминированная разбивка, слово-в-слово, без LLM
    if text_mode == "verbatim":
        log.info("textfit_verbatim")
        return split_fallback(title, text)
    chain = _provider_chain(provider)
    if not chain:
        log.info("textfit_fallback", reason="no_llm")
        return split_fallback(title, text)
    system = (
        _build_gentle_system(intrigue, compression) if text_mode == "gentle"
        else _build_adapt_system(intrigue, compression)
    )
    user = f"ЗАГОЛОВОК:\n{title}\n\nТЕКСТ:\n{text}"
    for prov in chain:
        try:
            slides = _coerce_slides(_extract_json(await _llm_generate(system, user, prov)))
            log.info("textfit_llm_ok", provider=prov, mode=text_mode, intrigue=intrigue, compression=compression)
            return slides
        except Exception as e:
            log.warning("textfit_llm_provider_failed", provider=prov, error=str(e)[:160])
    log.warning("textfit_all_providers_failed_fallback")
    return split_fallback(title, text)


async def refine(
    heading: str, body: str, *, action: str = "strengthen",
    instruction: str | None = None, provider: str | None = None,
) -> tuple[str, str]:
    chain = _provider_chain(provider)
    if not chain:
        return refine_fallback(heading, body, action)
    desc = _ACTION_DESC.get(action, _ACTION_DESC["strengthen"])
    if instruction:
        desc += f". Доп. указание: {instruction}"
    system = (
        "Ты — редактор Instagram-карусели. Переработай ОДИН слайд (заголовок + тело).\n"
        f"Действие: {desc}.\n"
        "Сохраняй стиль (эмоция, разговорность, капс/маскировка). Лимиты: heading ≤ 90 симв, "
        "body ≤ 320 симв. Язык русский.\n"
        'Верни СТРОГО JSON без markdown: {"heading":"...","body":"..."}'
    )
    user = f"ЗАГОЛОВОК: {heading}\nТЕЛО: {body}"
    for prov in chain:
        try:
            data = _extract_json(await _llm_generate(system, user, prov, max_tokens=600))
            return (data.get("heading") or heading).strip(), (data.get("body") or body).strip()
        except Exception as e:
            log.warning("refine_llm_provider_failed", provider=prov, error=str(e)[:160])
    return refine_fallback(heading, body, action)
