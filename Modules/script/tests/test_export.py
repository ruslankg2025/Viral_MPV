"""Unit-тесты export.to_markdown / to_json."""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from export import to_json, to_markdown  # noqa: E402
from schemas import (  # noqa: E402
    BodyScene,
    CtaSection,
    HookSection,
    ScriptBody,
    ScriptMeta,
)


def _body() -> ScriptBody:
    return ScriptBody(
        meta=ScriptMeta(
            template="reels_hook_v1",
            template_version="v1",
            language="ru",
            target_duration_sec=30,
            format="reels",
        ),
        hook=HookSection(text="Hook!", estimated_duration_sec=3.0),
        body=[
            BodyScene(scene=1, text="Scene one", estimated_duration_sec=10.0, visual_hint="wide shot"),
            BodyScene(scene=2, text="Scene two", estimated_duration_sec=13.0, visual_hint=""),
        ],
        cta=CtaSection(text="Subscribe!", estimated_duration_sec=4.0),
        hashtags=["#a", "#b", "#c"],
    )


def test_markdown_contains_all_sections():
    md = to_markdown(_body(), topic="Тема")
    assert "# Тема" in md
    assert "## Hook (3.0s)" in md
    assert "Hook!" in md
    assert "### Scene 1 (10.0s)" in md
    assert "Scene one" in md
    assert "> Visual: wide shot" in md
    assert "### Scene 2 (13.0s)" in md
    assert "## CTA (4.0s)" in md
    assert "Subscribe!" in md
    assert "#a #b #c" in md


def test_markdown_without_topic_uses_template():
    md = to_markdown(_body())
    assert md.startswith("# reels_hook_v1")


def test_json_is_valid_and_roundtrips():
    j = to_json(_body())
    parsed = json.loads(j)
    assert parsed["_schema_version"] == "1.0"
    assert parsed["hook"]["text"] == "Hook!"
    assert parsed["meta"]["template"] == "reels_hook_v1"
    assert len(parsed["body"]) == 2
