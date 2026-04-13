"""Unit-тесты constraints engine."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from constraints import validate  # noqa: E402
from schemas import (  # noqa: E402
    BodyScene,
    CtaSection,
    GenerateParams,
    HookSection,
    ScriptBody,
    ScriptMeta,
)


def _make_body(
    *,
    hook_sec: float = 3.0,
    scenes_sec: list[float] | None = None,
    cta_sec: float = 4.0,
    hashtags: list[str] | None = None,
    target: int = 30,
) -> ScriptBody:
    scenes_sec = scenes_sec if scenes_sec is not None else [10.0, 10.0]
    hashtags = hashtags if hashtags is not None else ["#a", "#b", "#c"]
    return ScriptBody(
        meta=ScriptMeta(
            template="reels_hook_v1",
            template_version="v1",
            language="ru",
            target_duration_sec=target,
            format="reels",
        ),
        hook=HookSection(text="Hook text", estimated_duration_sec=hook_sec),
        body=[
            BodyScene(
                scene=i + 1,
                text=f"scene {i + 1} text",
                estimated_duration_sec=s,
                visual_hint="",
            )
            for i, s in enumerate(scenes_sec)
        ],
        cta=CtaSection(text="CTA text", estimated_duration_sec=cta_sec),
        hashtags=hashtags,
    )


def _make_params(duration_sec: int = 30) -> GenerateParams:
    return GenerateParams(topic="test", duration_sec=duration_sec)


def test_valid_body_passes():
    report = validate(_make_body(target=30), _make_params(30))
    assert report.passed is True
    assert len(report.hard_violations) == 0


def test_duration_out_of_range_is_hard():
    # total = 3 + 10+10 + 4 = 27s, target 50 → вне [42.5, 57.5]
    report = validate(_make_body(target=50), _make_params(50))
    assert report.passed is False
    codes = {v.code for v in report.hard_violations}
    assert "duration_out_of_range" in codes


def test_empty_hook_is_hard():
    body = _make_body()
    body.hook.text = ""
    report = validate(body, _make_params(30))
    assert report.passed is False
    assert any(v.code == "hook_empty" for v in report.hard_violations)


def test_too_few_scenes_is_hard():
    body = _make_body(scenes_sec=[20.0])  # 1 сцена
    report = validate(body, _make_params(30))
    assert any(v.code == "body_too_few_scenes" for v in report.hard_violations)


def test_hook_too_long_is_soft():
    body = _make_body(hook_sec=7.0, scenes_sec=[8.0, 8.0], cta_sec=3.0)
    # total 26s, target 30, ok по duration
    report = validate(body, _make_params(30))
    # duration ok, hook > 5s — soft warning
    assert not any(v.code == "duration_out_of_range" for v in report.violations)
    assert any(v.code == "hook_too_long" and v.severity == "soft" for v in report.violations)
    assert report.passed is True  # soft не блокирует


def test_hashtags_format_soft():
    body = _make_body(hashtags=["#ok", "bad one", "#good"])
    report = validate(body, _make_params(30))
    assert any(v.code == "hashtag_format_invalid" and v.severity == "soft" for v in report.violations)
    assert report.passed is True


def test_hashtags_count_soft():
    body = _make_body(hashtags=["#a"])
    report = validate(body, _make_params(30))
    assert any(v.code == "hashtags_count_out_of_range" for v in report.violations)


def test_max_total_chars_hard():
    long_text = "x" * 9000
    body = _make_body()
    body.body[0].text = long_text
    report = validate(body, _make_params(30))
    assert any(v.code == "max_total_chars_exceeded" for v in report.hard_violations)
