"""Валидация сгенерированного ScriptBody против параметров запроса.

Hard violations — fail генерации (генератор может сделать один retry).
Soft violations — записываются в report, но не блокируют.
"""
import re

from schemas import ConstraintsReport, ConstraintViolation, ScriptBody, GenerateParams

DURATION_TOLERANCE = 0.15
MIN_BODY_SCENES = 2
MAX_TOTAL_CHARS = 8000
HOOK_MAX_DURATION_SEC = 5.0
HASHTAGS_MIN = 3
HASHTAGS_MAX = 10

# Эмодзи (TOV Руслана запрещает) — основные emoji-блоки, без ложных срабатываний
# на стрелки/математику.
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U0001F1E6-\U0001F1FF"
    "\U00002600-\U000026FF\U00002700-\U000027BF\U0000FE0F]"
)

# Паника/катастрофизация/кликбейт (TOV: без запугивания). soft-флаг на
# сенсационных поверхностях (хук/варианты/CTA/вспышки).
_PANIC_MARKERS = (
    "обанкрот", "разорит", "разорят", "разорение", "потеряете всё", "потеряешь всё",
    "потерять всё", "потеряете все", "потеряешь все", "останетесь ни с чем",
    "останешься ни с чем", "всё пропало", "все пропало", "катастроф", "крах",
    "срочно", "пока не поздно", "последний шанс", "успей", "кошмар", "паник",
    "бегите", "спасайте",
)

# Утверждение непроверенного как факта / число из тела вброса. Триггеры: «в N раз»,
# «на N%», глаголы-«случилось», действия властей. Разбор-фрейминг (вопрос, слух,
# «на самом деле», опровержение-норма) снимает флаг.
_ASSERT_RE = re.compile(
    r"(в\s+\d+([.,]\d+)?\s+раз)"
    r"|(на\s+\d+([.,]\d+)?\s*%)"
    r"|\b(вырос\w*|выросл\w*|подскочил\w*|взлетел\w*|рухнул\w*|обвал\w*|подняли|"
    r"повыс\w*|увеличил\w*|запрет\w*)\b",
    re.IGNORECASE,
)
_AUTHORITY_RE = re.compile(
    r"(власт|правительств|госдум|минфин|\bцб\b|госуд)\w*.{0,30}?"
    r"(принял|запрет|ввёл|ввел|ввели|повыс|обязал|заставил)",
    re.IGNORECASE,
)
_FACT_FRAMING_RE = re.compile(
    r"(разгон|разбор|разбир|разбер|правда ли|слух|якобы|спорят|миф|провер|"
    r"на самом деле|так ли|будто|\bмол\b|если верить|не может|невозможно|ограничен|"
    r"по закону|по ст|не вырос|вопрос|откуда|почему|зачем|насколько|\?)",
    re.IGNORECASE,
)


def validate(body: ScriptBody, params: GenerateParams) -> ConstraintsReport:
    violations: list[ConstraintViolation] = []

    _check_required_sections(body, violations)
    _check_duration(body, params.duration_sec, violations)
    _check_body_scenes(body, violations)
    _check_max_chars(body, violations)
    _check_hook_duration(body, violations)
    _check_hashtags(body, violations)
    _check_caption_track(body, params.duration_sec, violations)
    _check_no_emoji(body, violations)
    _check_tov_style(body, violations)
    _check_panic(body, violations)
    _check_unverified_as_fact(body, violations)

    passed = not any(v.severity == "hard" for v in violations)
    return ConstraintsReport(passed=passed, violations=violations)


def _check_panic(body: ScriptBody, out: list[ConstraintViolation]) -> None:
    """Паника/катастрофизация на сенсационных поверхностях (soft, по решению —
    не блокируем, помечаем в отчёте)."""
    surfaces = [body.hook.text, body.cta.text]
    surfaces += [v.text for v in body.hook_variants]
    surfaces += [f.text for f in body.caption_track]
    joined = " ".join(t or "" for t in surfaces).lower()
    hits = [m for m in _PANIC_MARKERS if m in joined]
    if hits:
        out.append(ConstraintViolation(
            code="panic_language",
            severity="soft",
            message=f"паника/катастрофизация в хуке/финале: {', '.join(hits[:5])} — TOV запрещает запугивание",
        ))


def _check_unverified_as_fact(body: ScriptBody, out: list[ConstraintViolation]) -> None:
    """Спорное утверждение/число подано как факт без разбор-фрейминга (soft).
    Разбор-режим («правда ли», «разберём», вопрос, опровержение-норма) снимает флаг."""
    fields: list[str] = [body.hook.text]
    fields += [v.text for v in body.hook_variants]
    fields += [s.text for s in body.body]
    flagged: list[str] = []
    for txt in fields:
        t = (txt or "").strip()
        if not t:
            continue
        asserts = bool(_ASSERT_RE.search(t) or _AUTHORITY_RE.search(t))
        if asserts and not _FACT_FRAMING_RE.search(t):
            flagged.append(t[:120])
    if flagged:
        out.append(ConstraintViolation(
            code="unverified_claim_as_fact",
            severity="soft",
            message=(
                "спорный тезис/число подан как факт без разбора: "
                + " | ".join(f"«{s}»" for s in flagged[:2])
                + " — оформить как разбор («правда ли…», «разберём») или занести в needs_factcheck"
            ),
        ))


def _check_caption_track(body: ScriptBody, target_sec: int, out: list[ConstraintViolation]) -> None:
    # Ждём дорожку слов-вспышек ~1 слово/1.5–2с; сильно мало → soft-флаг (не блок).
    n = len(body.caption_track)
    expected_min = max(6, int(target_sec / 3))
    if n < expected_min:
        out.append(
            ConstraintViolation(
                code="caption_track_sparse",
                severity="soft",
                message=f"caption_track: {n} вспышек, ожидалось >= {expected_min} для {target_sec}s",
            )
        )


def _script_texts(body: ScriptBody) -> str:
    parts = [body.hook.text, body.cta.text, body.description]
    parts += [s.text for s in body.body]
    parts += [f.text for f in body.caption_track]
    return " ".join(t or "" for t in parts)


def _check_no_emoji(body: ScriptBody, out: list[ConstraintViolation]) -> None:
    if _EMOJI_RE.search(_script_texts(body)):
        out.append(
            ConstraintViolation(
                code="emoji_present",
                severity="soft",
                message="в тексте есть эмодзи — TOV запрещает",
            )
        )


def _check_tov_style(body: ScriptBody, out: list[ConstraintViolation]) -> None:
    # Чек-лист TOV Руслана (soft, для видимости в отчёте): без «!» и длинных тире.
    joined = _script_texts(body)
    if "!" in joined:
        out.append(ConstraintViolation(
            code="exclamation_present", severity="soft",
            message="восклицательные знаки — TOV запрещает"))
    if "—" in joined:  # em-dash «—»
        out.append(ConstraintViolation(
            code="em_dash_present", severity="soft",
            message="длинные тире (—) — TOV запрещает"))


def _check_required_sections(body: ScriptBody, out: list[ConstraintViolation]) -> None:
    if not body.hook.text.strip():
        out.append(ConstraintViolation(code="hook_empty", severity="hard", message="hook.text is empty"))
    # Пустой CTA — ВАЛИДЕН: открытый финал теперь дефолт (TOV Руслана, Часть 5),
    # закрывающая мысль уходит в последнюю сцену, а не в «Подпишитесь на канал».
    if not body.body:
        out.append(ConstraintViolation(code="body_empty", severity="hard", message="body has no scenes"))


def _check_duration(body: ScriptBody, target_sec: int, out: list[ConstraintViolation]) -> None:
    # severity=soft (осознанно): estimated_duration_sec — это САМООЦЕНКА модели,
    # а не измеренная длительность. gpt-4o систематически занижает её, и раньше
    # hard-отказ браковал вполне годные сценарии — до пользователя не доезжало
    # НИЧЕГО, а каждый провал стоил 2 LLM-вызова (генерация + холостой retry).
    # Теперь несоответствие длины — предупреждение: сценарий проходит, а на
    # карточке видно, что он короче/длиннее цели. Жёсткие проверки (пустой
    # hook/cta/body, мало сцен, переполнение) по-прежнему блокируют брак.
    total = body.hook.estimated_duration_sec + body.cta.estimated_duration_sec
    total += sum(s.estimated_duration_sec for s in body.body)
    low = target_sec * (1 - DURATION_TOLERANCE)
    high = target_sec * (1 + DURATION_TOLERANCE)
    if not (low <= total <= high):
        out.append(
            ConstraintViolation(
                code="duration_out_of_range",
                severity="soft",
                message=(
                    f"total duration {total:.1f}s not in [{low:.1f}, {high:.1f}] "
                    f"(target {target_sec}s ± {int(DURATION_TOLERANCE*100)}%)"
                ),
            )
        )


def _check_body_scenes(body: ScriptBody, out: list[ConstraintViolation]) -> None:
    if len(body.body) < MIN_BODY_SCENES:
        out.append(
            ConstraintViolation(
                code="body_too_few_scenes",
                severity="hard",
                message=f"body has {len(body.body)} scenes, min {MIN_BODY_SCENES}",
            )
        )


def _check_max_chars(body: ScriptBody, out: list[ConstraintViolation]) -> None:
    total_chars = len(body.hook.text) + len(body.cta.text)
    total_chars += sum(len(s.text) for s in body.body)
    if total_chars > MAX_TOTAL_CHARS:
        out.append(
            ConstraintViolation(
                code="max_total_chars_exceeded",
                severity="hard",
                message=f"total text length {total_chars} > {MAX_TOTAL_CHARS}",
            )
        )


def _check_hook_duration(body: ScriptBody, out: list[ConstraintViolation]) -> None:
    if body.hook.estimated_duration_sec > HOOK_MAX_DURATION_SEC:
        out.append(
            ConstraintViolation(
                code="hook_too_long",
                severity="soft",
                message=f"hook {body.hook.estimated_duration_sec}s > {HOOK_MAX_DURATION_SEC}s",
            )
        )


def _check_hashtags(body: ScriptBody, out: list[ConstraintViolation]) -> None:
    n = len(body.hashtags)
    if n < HASHTAGS_MIN or n > HASHTAGS_MAX:
        out.append(
            ConstraintViolation(
                code="hashtags_count_out_of_range",
                severity="soft",
                message=f"hashtags count {n} not in [{HASHTAGS_MIN}, {HASHTAGS_MAX}]",
            )
        )
    for tag in body.hashtags:
        if not tag.startswith("#") or " " in tag:
            out.append(
                ConstraintViolation(
                    code="hashtag_format_invalid",
                    severity="soft",
                    message=f"invalid hashtag: {tag!r}",
                )
            )
            break
