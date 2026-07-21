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

# конец предложения: .!?… с возможными закрывающими кавычками/скобками, затем пробел
_SENT_SPLIT = re.compile(r'(?<=[.!?…])[»"”)\]]*\s+')
_ENUM = re.compile(r"^\s*\d+[.)]\s*")
_BULLET = re.compile(r"^\s*[—–\-•*·]+\s+")
# строки-мусор из исходника (чат-экспорт, метки-заголовки, счётчики знаков)
_NOISE = (
    re.compile(r"^\s*\[[^\]]{0,60}\]\s*[^:]{1,40}:"),      # [02.07 14:37] Имя:
    re.compile(r"^\s*[A-ZА-ЯЁ][A-ZА-ЯЁ \-]{1,24}:\s*$"),   # ЗАГОЛОВОК: / ТЕКСТ:
    re.compile(r"^\s*\d+\s*зн(ак|аk)", re.I),              # 1772 знака
)


# ─── детерминированный fallback (verbatim / без ключа) ────────────────────────
def _sentences(p: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split((p or "").strip()) if s.strip()]


def _strip_markers(s: str) -> str:
    return _BULLET.sub("", _ENUM.sub("", (s or "").strip())).strip()


def _clean_paragraphs(text: str) -> list[str]:
    out = []
    for raw in re.split(r"\n+", text or ""):
        s = raw.strip()
        if not s or any(p.search(s) for p in _NOISE):
            continue
        s = _strip_markers(s)
        if s:
            out.append(s)
    return out


def _distribute(sents: list[str], n: int) -> list[list[str]]:
    """Разложить предложения в ~n групп примерно поровну по длине."""
    if not sents:
        return []
    total = sum(len(s) + 1 for s in sents)
    target = max(1.0, total / n)
    groups: list[list[str]] = []
    cur: list[str] = []
    acc = 0
    for i, s in enumerate(sents):
        cur.append(s)
        acc += len(s) + 1
        remaining = len(sents) - i - 1
        if acc >= target and len(groups) < n - 1 and remaining >= (n - 1 - len(groups)):
            groups.append(cur)
            cur, acc = [], 0
    if cur:
        groups.append(cur)
    return groups


def _split_head(block: str) -> tuple[str, str]:
    """Заголовок = первое предложение (короткое, если есть продолжение), иначе пусто.
    Одно предложение целиком уходит в тело (без дублирования)."""
    sents = _sentences(block)
    if len(sents) >= 2 and len(sents[0]) <= 70:
        return sents[0], " ".join(sents[1:])
    return "", block


def _group_numbered(raw: list[str]) -> list[str]:
    """Склеить строки в блоки по нумерации: строка с «1.» открывает новый пункт,
    остальные — продолжение текущего.

    Мягкие переносы внутри пункта (перевод строки посреди предложения — обычное
    дело для текста из мессенджера) раньше не совпадали с _ENUM и выбрасывались
    молча: пункт обрывался на первом же \\n. Строки до первого номера — вступление,
    оно уходит в начало первого пункта, а не в мусор.
    """
    blocks: list[str] = []
    pre: list[str] = []
    for line in raw:
        if _ENUM.match(line):
            blocks.append(_strip_markers(line))
        elif blocks:
            blocks[-1] = f"{blocks[-1]} {line}".strip()
        else:
            pre.append(line)
    if pre and blocks:
        blocks[0] = f"{' '.join(pre)} {blocks[0]}".strip()
    return blocks


def _extract_blocks(title: str, text: str) -> tuple[str, str, list[str]]:
    """Заголовок → (хук, подзаголовок); Текст → до 5 блоков дословно (мусор вырезан).
    Нумерованные пункты (1. 2. …) уважаются как блоки; иначе — баланс предложений."""
    tlines = [_strip_markers(ln) for ln in (title or "").splitlines() if ln.strip()]
    main = tlines[0] if tlines else _strip_markers(title or "")
    sub = " ".join(tlines[1:]) if len(tlines) > 1 else ""
    raw = [p.strip() for p in re.split(r"\n+", text or "")
           if p.strip() and not any(x.search(p.strip()) for x in _NOISE)]
    numbered = [p for p in raw if _ENUM.match(p)]
    if len(numbered) >= 3:
        # без [:5] — лишние пункты сливаются ниже, а не отбрасываются
        blocks = _group_numbered(raw)
    else:
        sents = [s for p in _clean_paragraphs(text) for s in _sentences(p)]
        blocks = [" ".join(g) for g in _distribute(sents, 5)]
    if len(blocks) > 5:
        blocks = blocks[:4] + [" ".join(blocks[4:])]
    return main, sub, blocks


def _base_slides(main: str, sub: str, points: list[tuple[str, str]]) -> list[dict]:
    slides = [{"idx": 1, "role": "hook", "heading": main, "body": sub}]
    for i, (h, b) in enumerate(points, start=2):
        slides.append({"idx": i, "role": "point", "heading": h, "body": b})
    while len(slides) < 6:
        slides.append({"idx": len(slides) + 1, "role": "point", "heading": "", "body": ""})
    slides.append({"idx": 7, "role": "cta", "heading": "", "body": ""})  # → _apply_cta в роутере
    return slides[:7]


def split_fallback(title: str, text: str) -> list[dict]:
    main, sub, blocks = _extract_blocks(title, text)
    return _base_slides(main, sub, [_split_head(b) for b in blocks])


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
async def _gentle_slides(title: str, text: str, chain: list[str]) -> list[dict]:
    """Бережно: блоки 1:1 (тело автора дословно, целиком), LLM придумывает только заголовки.
    Не переразбивает, не пропускает и не режет текст."""
    main, sub, blocks = _extract_blocks(title, text)
    slides = _base_slides(main, sub, [("", b) for b in blocks])
    pts = [s for s in slides if s["role"] == "point" and s["body"].strip()]
    if pts:
        system = ("Ты — редактор Instagram-каруселей. Для КАЖДОГО пронумерованного абзаца придумай короткий "
                  "цепляющий заголовок из 3–6 слов (можно фразой из самого абзаца), НЕ меняя текст абзаца. "
                  'Верни СТРОГО JSON: {"headings":["...", ...]} — ровно по числу абзацев, в том же порядке.')
        user = "\n\n".join(f"{i+1}) {s['body']}" for i, s in enumerate(pts))
        for prov in chain:
            try:
                data = _extract_json(await _llm_generate(system, user, prov, max_tokens=400))
                heads = data.get("headings") if isinstance(data, dict) else None
                if isinstance(heads, list) and heads:
                    for s, h in zip(pts, heads):
                        if isinstance(h, str) and h.strip():
                            s["heading"] = h.strip()
                    log.info("gentle_headings_ok", provider=prov, n=len(pts))
                    return slides
            except Exception as e:
                log.warning("gentle_headings_failed", provider=prov, error=str(e)[:140])
    return slides  # без LLM — заголовки пустые, тело дословное


async def adapt(
    title: str, text: str, *, provider: str | None = None,
    intrigue: str = "mid", compression: str = "mid", text_mode: str = "ai",
) -> list[dict]:
    # verbatim — детерминированная разбивка, слово-в-слово, без LLM
    if text_mode == "verbatim":
        log.info("textfit_verbatim")
        return split_fallback(title, text)
    chain = _provider_chain(provider)
    # gentle — блоки 1:1 дословно + LLM только заголовки (без переразбивки/урезки)
    if text_mode == "gentle":
        if not chain:
            return split_fallback(title, text)
        log.info("textfit_gentle")
        return await _gentle_slides(title, text, chain)
    # ai — полная адаптация (переписывает + сокращает)
    if not chain:
        log.info("textfit_fallback", reason="no_llm")
        return split_fallback(title, text)
    system = _build_adapt_system(intrigue, compression)
    user = f"ЗАГОЛОВОК:\n{title}\n\nТЕКСТ:\n{text}"
    for prov in chain:
        try:
            slides = _coerce_slides(_extract_json(await _llm_generate(system, user, prov)))
            log.info("textfit_llm_ok", provider=prov, mode="ai", intrigue=intrigue, compression=compression)
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
