"""Pydantic модели запроса/ответа + ScriptBody schema v1."""
from typing import Any, Literal

from pydantic import BaseModel, Field

SCRIPT_SCHEMA_VERSION = "1.0"

Status = Literal["ok", "validation_failed", "error"]
FormatKind = Literal["reels", "shorts", "long"]


class GenerateParams(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)
    duration_sec: int = Field(..., ge=5, le=3600)
    language: str = Field(default="ru")
    format: FormatKind = Field(default="reels")
    tone: str | None = None
    pattern_hint: str | None = None  # free-text аналог A3.6 до появления Registry
    extra: dict[str, Any] = Field(default_factory=dict)


class GenerateReq(BaseModel):
    template: str = Field(..., min_length=1, max_length=100)
    template_version: str | None = None  # None → активная версия шаблона
    profile: dict[str, Any] = Field(default_factory=dict)
    params: GenerateParams
    provider: str | None = None  # override default_text_provider


class ForkReq(BaseModel):
    override: dict[str, Any] = Field(default_factory=dict)
    # override поддерживает ключи: template, template_version, profile, params, provider


class HookSection(BaseModel):
    text: str
    estimated_duration_sec: float = Field(ge=0)


class HookVariant(BaseModel):
    """Альтернативная зацепка для A/B-тестов."""
    text: str
    technique: str = ""           # «Психологический контраст», «Шок-факт», ...
    estimated_duration_sec: float = Field(default=0.0, ge=0)
    rating: Literal["best", "strong", "alternative", "weak"] = "alternative"


class BodyScene(BaseModel):
    scene: int
    text: str
    estimated_duration_sec: float = Field(ge=0)
    visual_hint: str = ""


class CtaSection(BaseModel):
    text: str
    estimated_duration_sec: float = Field(ge=0)


class EditorBriefSegment(BaseModel):
    """Один таймкод-сегмент для монтажёра."""
    time_range: str                # «0:00–0:03»
    visual: str = ""               # описание визуала
    text_on_screen: str = ""       # подпись на кадре
    transition: str = ""           # «резкий кат», «fade in», ...


class EditorBrief(BaseModel):
    """Подробная инструкция монтажёру: формат, длительность, посегментный план."""
    format_hint: str = ""          # «верхние 30% — говорящая голова, нижние 70% — визуал»
    duration_sec: int = 0
    segments: list[EditorBriefSegment] = Field(default_factory=list)


class ScriptMeta(BaseModel):
    template: str
    template_version: str
    language: str
    target_duration_sec: int
    format: FormatKind


class ScriptBody(BaseModel):
    meta: ScriptMeta
    hook: HookSection
    body: list[BodyScene] = Field(default_factory=list)
    cta: CtaSection
    # Новые опциональные блоки (Track scenario v2)
    hook_variants: list[HookVariant] = Field(default_factory=list)
    description: str = ""              # подпись к посту (caption)
    editor_brief: EditorBrief | None = None
    hashtags: list[str] = Field(default_factory=list)
    schema_version: str = Field(default=SCRIPT_SCHEMA_VERSION, alias="_schema_version")

    model_config = {"populate_by_name": True}


class ConstraintViolation(BaseModel):
    code: str
    severity: Literal["hard", "soft"]
    message: str


class ConstraintsReport(BaseModel):
    passed: bool
    violations: list[ConstraintViolation] = Field(default_factory=list)

    @property
    def hard_violations(self) -> list[ConstraintViolation]:
        return [v for v in self.violations if v.severity == "hard"]


class ScriptVersionPublic(BaseModel):
    id: str
    root_id: str
    parent_id: str | None
    template: str
    template_version: str
    schema_version: str
    status: Status
    body: ScriptBody
    params: dict[str, Any]
    profile: dict[str, Any]
    constraints_report: ConstraintsReport | None
    cost_usd: float
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None
    provider: str
    model: str
    created_at: str
