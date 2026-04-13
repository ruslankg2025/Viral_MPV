"""Прайсы провайдеров в USD. Обновлять вручную при изменении тарифов.

Ключ первого уровня — provider id (тот же, что в api_keys.provider).
Для транскрипции — `audio_per_hour` или `audio_per_minute`.
Для vision/LLM — `input_per_1m` и `output_per_1m` в USD / 1M tokens.
"""

from typing import Any

PRICING: dict[str, dict[str, Any]] = {
    # ---------- transcription ----------
    "assemblyai": {
        "kind": "transcription",
        "default_model": "best",
        "models": {
            "best": {"audio_per_hour": 0.37},
            "nano": {"audio_per_hour": 0.12},
        },
    },
    "deepgram": {
        "kind": "transcription",
        "default_model": "nova-3",
        "models": {
            "nova-3": {"audio_per_minute": 0.0043},
            "nova-2": {"audio_per_minute": 0.0043},
        },
    },
    "openai_whisper": {
        "kind": "transcription",
        "default_model": "whisper-1",
        "models": {
            "whisper-1": {"audio_per_minute": 0.006},
        },
    },
    "groq_whisper": {
        "kind": "transcription",
        "default_model": "whisper-large-v3",
        "models": {
            "whisper-large-v3": {"audio_per_hour": 0.04},
            "whisper-large-v3-turbo": {"audio_per_hour": 0.04},
        },
    },
    # ---------- vision / llm ----------
    "anthropic_claude": {
        "kind": "vision",
        "default_model": "claude-sonnet-4-6",
        "models": {
            "claude-sonnet-4-6": {"input_per_1m": 3.0, "output_per_1m": 15.0},
            "claude-opus-4-6": {"input_per_1m": 15.0, "output_per_1m": 75.0},
        },
    },
    "openai_gpt4o": {
        "kind": "vision",
        "default_model": "gpt-4o",
        "models": {
            "gpt-4o": {"input_per_1m": 2.5, "output_per_1m": 10.0},
        },
    },
    "openai_gpt4o_mini": {
        "kind": "vision",
        "default_model": "gpt-4o-mini",
        "models": {
            "gpt-4o-mini": {"input_per_1m": 0.15, "output_per_1m": 0.60},
        },
    },
    "google_gemini_pro": {
        "kind": "vision",
        "default_model": "gemini-2.5-pro",
        "models": {
            "gemini-2.5-pro": {"input_per_1m": 1.25, "output_per_1m": 5.0},
        },
    },
    "google_gemini_flash": {
        "kind": "vision",
        "default_model": "gemini-2.5-flash",
        "models": {
            "gemini-2.5-flash": {"input_per_1m": 0.075, "output_per_1m": 0.30},
        },
    },
    # ---------- text generation (same api endpoint / billing as vision) ----------
    "anthropic_claude_text": {
        "kind": "vision",
        "default_model": "claude-sonnet-4-6",
        "models": {
            "claude-sonnet-4-6": {"input_per_1m": 3.0, "output_per_1m": 15.0},
            "claude-opus-4-6": {"input_per_1m": 15.0, "output_per_1m": 75.0},
        },
    },
    "openai_gpt4o_text": {
        "kind": "vision",
        "default_model": "gpt-4o",
        "models": {
            "gpt-4o": {"input_per_1m": 2.5, "output_per_1m": 10.0},
            "gpt-4o-mini": {"input_per_1m": 0.15, "output_per_1m": 0.60},
        },
    },
}

TRANSCRIPTION_PROVIDERS = [p for p, v in PRICING.items() if v["kind"] == "transcription"]
VISION_PROVIDERS = [p for p, v in PRICING.items() if v["kind"] == "vision"]
ALL_PROVIDERS = list(PRICING.keys())


def provider_kind(provider: str) -> str:
    if provider not in PRICING:
        raise ValueError(f"unknown provider: {provider}")
    return PRICING[provider]["kind"]


def default_model(provider: str) -> str:
    return PRICING[provider]["default_model"]


def estimate_cost(
    provider: str,
    model: str,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    audio_seconds: float | None = None,
) -> float:
    if provider not in PRICING:
        return 0.0
    models = PRICING[provider]["models"]
    price = models.get(model) or models.get(PRICING[provider]["default_model"]) or {}

    if audio_seconds is not None:
        if "audio_per_hour" in price:
            return (audio_seconds / 3600.0) * price["audio_per_hour"]
        if "audio_per_minute" in price:
            return (audio_seconds / 60.0) * price["audio_per_minute"]
        return 0.0

    cost = 0.0
    if input_tokens and "input_per_1m" in price:
        cost += (input_tokens / 1_000_000.0) * price["input_per_1m"]
    if output_tokens and "output_per_1m" in price:
        cost += (output_tokens / 1_000_000.0) * price["output_per_1m"]
    return cost
