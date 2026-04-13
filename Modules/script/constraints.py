"""Валидация сгенерированного ScriptBody против параметров запроса.

Hard violations — fail генерации (генератор может сделать один retry).
Soft violations — записываются в report, но не блокируют.
"""
from schemas import ConstraintsReport, ConstraintViolation, ScriptBody, GenerateParams

DURATION_TOLERANCE = 0.15
MIN_BODY_SCENES = 2
MAX_TOTAL_CHARS = 8000
HOOK_MAX_DURATION_SEC = 5.0
HASHTAGS_MIN = 3
HASHTAGS_MAX = 10


def validate(body: ScriptBody, params: GenerateParams) -> ConstraintsReport:
    violations: list[ConstraintViolation] = []

    _check_required_sections(body, violations)
    _check_duration(body, params.duration_sec, violations)
    _check_body_scenes(body, violations)
    _check_max_chars(body, violations)
    _check_hook_duration(body, violations)
    _check_hashtags(body, violations)

    passed = not any(v.severity == "hard" for v in violations)
    return ConstraintsReport(passed=passed, violations=violations)


def _check_required_sections(body: ScriptBody, out: list[ConstraintViolation]) -> None:
    if not body.hook.text.strip():
        out.append(ConstraintViolation(code="hook_empty", severity="hard", message="hook.text is empty"))
    if not body.cta.text.strip():
        out.append(ConstraintViolation(code="cta_empty", severity="hard", message="cta.text is empty"))
    if not body.body:
        out.append(ConstraintViolation(code="body_empty", severity="hard", message="body has no scenes"))


def _check_duration(body: ScriptBody, target_sec: int, out: list[ConstraintViolation]) -> None:
    total = body.hook.estimated_duration_sec + body.cta.estimated_duration_sec
    total += sum(s.estimated_duration_sec for s in body.body)
    low = target_sec * (1 - DURATION_TOLERANCE)
    high = target_sec * (1 + DURATION_TOLERANCE)
    if not (low <= total <= high):
        out.append(
            ConstraintViolation(
                code="duration_out_of_range",
                severity="hard",
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
