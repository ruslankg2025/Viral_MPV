"""Первичный посев ключей в KeyStore из явного конфига."""

import logging
from dataclasses import dataclass
from typing import Iterator

from .store import KeyStore

log = logging.getLogger("viral_llm.keys.bootstrap")


@dataclass(frozen=True)
class LLMBootstrapConfig:
    assemblyai_api_key: str = ""
    deepgram_api_key: str = ""
    openai_whisper_api_key: str = ""
    groq_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_gemini_api_key: str = ""


# Маппинг: поле LLMBootstrapConfig -> provider id
_BOOTSTRAP_MAP: dict[str, str] = {
    "assemblyai_api_key": "assemblyai",
    "deepgram_api_key": "deepgram",
    "openai_whisper_api_key": "openai_whisper",
    "groq_api_key": "groq_whisper",
    "anthropic_api_key": "anthropic_claude",
    "openai_api_key": "openai_gpt4o",
    "google_gemini_api_key": "google_gemini_pro",
}

# Ключи, которые под один реальный API-ключ размножаются на несколько
# pricing-entry (один вендор, один биллинг, несколько модальностей/моделей):
#  - anthropic_api_key -> anthropic_claude (vision) + anthropic_claude_text (text)
#  - openai_api_key    -> openai_gpt4o + openai_gpt4o_mini + openai_gpt4o_text
#  - google_gemini_api_key -> google_gemini_pro + google_gemini_flash
_FANOUT: dict[str, list[str]] = {
    "anthropic_api_key": ["anthropic_claude", "anthropic_claude_text"],
    "openai_api_key": ["openai_gpt4o", "openai_gpt4o_mini", "openai_gpt4o_text"],
    "google_gemini_api_key": ["google_gemini_pro", "google_gemini_flash"],
}


def bootstrap_from_config(cfg: LLMBootstrapConfig, store: KeyStore) -> int:
    """Создаёт ключи в KeyStore из LLMBootstrapConfig, если для провайдера
    ещё не было bootstrap. Идемпотентна: повторные запуски не создают дублей.
    Возвращает число созданных ключей."""
    created = 0
    existing_count = sum(store.count_active().values())

    for field, providers in _iter_fanout():
        secret = (getattr(cfg, field, "") or "").strip()
        if not secret:
            continue

        for provider in providers:
            if store.is_bootstrap_consumed(provider):
                continue

            label = f"bootstrap:{provider}"
            try:
                store.create(
                    provider=provider,
                    label=label,
                    secret=secret,
                    priority=100,
                    is_active=True,
                )
                store.mark_bootstrap_consumed(provider)
                created += 1
                log.info("bootstrap_created provider=%s label=%s", provider, label)
            except Exception as e:
                log.warning("bootstrap_skip provider=%s error=%s", provider, e)

    if created:
        log.info("bootstrap_done created=%d existing=%d", created, existing_count)
    else:
        log.info("bootstrap_noop existing=%d", existing_count)
    return created


def _iter_fanout() -> Iterator[tuple[str, list[str]]]:
    for field, provider in _BOOTSTRAP_MAP.items():
        if field in _FANOUT:
            yield field, _FANOUT[field]
        else:
            yield field, [provider]
