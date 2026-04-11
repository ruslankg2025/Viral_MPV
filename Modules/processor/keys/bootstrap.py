"""Первичный посев ключей из env BOOTSTRAP_* при старте."""

from config import Settings
from keys.store import KeyStore
from logging_setup import get_logger

log = get_logger("keys.bootstrap")


# Маппинг: env-поле в Settings -> provider id
BOOTSTRAP_MAP: dict[str, str] = {
    "bootstrap_assemblyai_api_key": "assemblyai",
    "bootstrap_deepgram_api_key": "deepgram",
    "bootstrap_openai_whisper_api_key": "openai_whisper",
    "bootstrap_groq_api_key": "groq_whisper",
    "bootstrap_anthropic_api_key": "anthropic_claude",
    "bootstrap_openai_api_key": "openai_gpt4o",
    "bootstrap_google_gemini_api_key": "google_gemini_pro",
}

# Некоторые ключи OpenAI/Google нужно размножить на несколько "логических" провайдеров,
# т.к. под один реальный API-ключ приходится несколько наших pricing-entry:
#  - bootstrap_openai_api_key -> openai_gpt4o + openai_gpt4o_mini (один биллинг)
#  - bootstrap_google_gemini_api_key -> google_gemini_pro + google_gemini_flash
FANOUT: dict[str, list[str]] = {
    "bootstrap_openai_api_key": ["openai_gpt4o", "openai_gpt4o_mini"],
    "bootstrap_google_gemini_api_key": ["google_gemini_pro", "google_gemini_flash"],
}


def bootstrap_from_env(settings: Settings, store: KeyStore) -> int:
    """
    Создаёт ключи в keys.db из BOOTSTRAP_* env, если для провайдера ещё не было bootstrap.
    Возвращает число созданных ключей. Идемпотентно: повторные запуски не создают дублей.
    """
    created = 0
    existing_count = sum(store.count_active().values())

    for env_field, providers in _iter_fanout():
        secret = getattr(settings, env_field, "") or ""
        if not secret.strip():
            continue

        for provider in providers:
            if store.is_bootstrap_consumed(provider):
                continue

            # Дополнительная проверка: нет ли уже ключа с этим label (UNIQUE constraint)
            label = f"bootstrap:{provider}"
            try:
                store.create(
                    provider=provider,
                    label=label,
                    secret=secret.strip(),
                    priority=100,
                    is_active=True,
                )
                store.mark_bootstrap_consumed(provider)
                created += 1
                log.info("bootstrap_created", provider=provider, label=label)
            except Exception as e:
                log.warning("bootstrap_skip", provider=provider, error=str(e))

    if created:
        log.info("bootstrap_done", created=created, existing=existing_count)
    else:
        log.info("bootstrap_noop", existing=existing_count)
    return created


def _iter_fanout():
    """Yield (env_field, [providers])."""
    for env_field, provider in BOOTSTRAP_MAP.items():
        if env_field in FANOUT:
            yield env_field, FANOUT[env_field]
        else:
            yield env_field, [provider]
