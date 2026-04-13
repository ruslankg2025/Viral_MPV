"""Экспорт ScriptBody в Markdown / JSON."""
import json

from schemas import ScriptBody


def to_markdown(body: ScriptBody, topic: str = "") -> str:
    lines: list[str] = []
    title = topic or body.meta.template
    lines.append(f"# {title}")
    lines.append("")
    lines.append(
        f"**Language:** {body.meta.language}  |  "
        f"**Target duration:** {body.meta.target_duration_sec}s  |  "
        f"**Format:** {body.meta.format}"
    )
    lines.append("")
    lines.append(f"## Hook ({body.hook.estimated_duration_sec}s)")
    lines.append(body.hook.text)
    lines.append("")
    lines.append("## Body")
    for scene in body.body:
        lines.append(f"### Scene {scene.scene} ({scene.estimated_duration_sec}s)")
        lines.append(scene.text)
        if scene.visual_hint:
            lines.append(f"> Visual: {scene.visual_hint}")
        lines.append("")
    lines.append(f"## CTA ({body.cta.estimated_duration_sec}s)")
    lines.append(body.cta.text)
    lines.append("")
    if body.hashtags:
        lines.append("---")
        lines.append(" ".join(body.hashtags))
    return "\n".join(lines)


def to_json(body: ScriptBody) -> str:
    return json.dumps(
        body.model_dump(by_alias=True),
        ensure_ascii=False,
        indent=2,
    )
