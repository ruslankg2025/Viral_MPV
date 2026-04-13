"""Доступ к промптам через Prompts Registry v2.

Если в state есть инициализированный prompt_store — читаем из БД (с поддержкой
версионирования и активных версий). Если store не инициализирован (например,
в юнит-тестах sub-функций) — fallback на встроенные константы как v1.
"""
from dataclasses import dataclass

from prompts.vision_default import VISION_DEFAULT
from prompts.vision_detailed import VISION_DETAILED
from prompts.vision_hooks import VISION_HOOKS_FOCUSED

# Соответствие name → встроенное тело по умолчанию (v1).
# Используется как seed для миграции в БД и как fallback.
BUILTIN_PROMPTS: dict[str, str] = {
    "vision_default": VISION_DEFAULT,
    "vision_detailed": VISION_DETAILED,
    "vision_hooks_focused": VISION_HOOKS_FOCUSED,
}

# Маппинг короткого `prompt_template` (как в payload) на полное `name`.
TEMPLATE_TO_NAME: dict[str, str] = {
    "default": "vision_default",
    "detailed": "vision_detailed",
    "hooks_focused": "vision_hooks_focused",
}


@dataclass(frozen=True)
class PromptRecord:
    name: str
    version: str
    body: str
    is_active: bool = True

    @property
    def full_version(self) -> str:
        """Идентификатор для cache_key и трейсинга: `vision_default:v1`."""
        return f"{self.name}:{self.version}"


def get_prompt_record(template: str, version: str | None = None) -> PromptRecord:
    """Достаёт промпт по короткому ключу template (default/detailed/hooks_focused).

    1. Преобразует template → name через TEMPLATE_TO_NAME.
    2. Если есть state.prompt_store — читает из БД.
    3. Иначе — fallback на встроенный BUILTIN_PROMPTS как v1.
    """
    name = TEMPLATE_TO_NAME.get(template, "vision_default")

    # Попытка прочитать из БД, если store инициализирован
    try:
        from state import state

        store = getattr(state, "prompt_store", None)
        if store is not None:
            record = store.get(name, version=version)
            if record is not None:
                return record
    except Exception:
        # В тестах state может не быть инициализирован — это ок
        pass

    # Fallback: встроенная v1
    body = BUILTIN_PROMPTS.get(name, BUILTIN_PROMPTS["vision_default"])
    return PromptRecord(name=name, version="v1", body=body, is_active=True)


def get_prompt(template: str) -> str:
    """Backward-compat shortcut, возвращает только тело промпта."""
    return get_prompt_record(template).body

