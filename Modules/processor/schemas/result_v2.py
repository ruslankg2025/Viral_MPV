"""Контракт ответа processor v2.

Этот модуль фиксирует структуру result, возвращаемого full_analysis и
sub-tasks. Новые поля по сравнению с v1:
  - analysis_version: версия схемы (для downstream-совместимости)
  - prompt_version: явная версия системного промпта (для A/B и кеша)
  - source_ref: opaque-словарь {platform, external_id} echo из payload
  - artifacts: явные пути к артефактам на shared volume — критично для
    отдельного сервиса analyzer, который читает vision-результат с диска

Все модели — pydantic v2, opt-in: задачи возвращают обычные dict-ы,
а схемы используются для валидации в тестах и для документации API.
"""
from typing import Any

from pydantic import BaseModel, Field

ANALYSIS_VERSION = "2.0"


class SourceRef(BaseModel):
    """Opaque-указатель на источник видео.

    Используется только для формирования стабильного cache_key и для echo
    в результат. Никакой семантики (views/likes/...) processor не интерпретирует.
    """

    platform: str
    external_id: str


class Artifacts(BaseModel):
    """Явные пути к артефактам на shared volume.

    Все пути опциональные — заполняются теми задачами, которые их создают.
    Downstream-сервисы (analyzer, A5, A6) должны читать артефакты по этим
    путям, а не угадывать конвенции.
    """

    audio_path: str | None = None
    frames_dir: str | None = None
    transcript_path: str | None = None
    vision_result_path: str | None = None


class CostBreakdown(BaseModel):
    transcription: float = 0.0
    vision: float = 0.0
    total: float = 0.0


class AnalysisResultV2(BaseModel):
    """Полный результат full_analysis по схеме v2.

    Используется как pydantic-модель в тестах и для документации API.
    Сами задачи возвращают dict — schema служит контрактом, а не runtime-валидатором,
    чтобы не ломать обратную совместимость с уже сохранёнными в jobs.db результатами.
    """

    analysis_version: str = ANALYSIS_VERSION
    prompt_version: str | None = None
    source_ref: SourceRef | None = None
    artifacts: Artifacts = Field(default_factory=Artifacts)
    transcript: dict[str, Any] | None = None
    transcript_error: str | None = None
    frames: dict[str, Any] | None = None
    vision: dict[str, Any] | None = None
    vision_error: str | None = None
    cost_usd: dict[str, float] = Field(default_factory=dict)
