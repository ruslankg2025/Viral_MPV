from dataclasses import dataclass
from pathlib import Path


@dataclass
class TranscriptResult:
    text: str
    language: str | None
    provider: str
    model: str
    duration_sec: float
    latency_ms: int


@dataclass
class VisionResult:
    raw_json: dict
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


@dataclass
class GenerationResult:
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class ProviderError(RuntimeError):
    """Ошибка вызова провайдера — триггерит fallback chain."""


class TranscriptionClient:
    provider: str
    default_model: str

    async def transcribe(
        self,
        *,
        audio_path: Path,
        api_key: str,
        language: str | None,
        model: str | None = None,
    ) -> TranscriptResult:
        raise NotImplementedError


class VisionClient:
    provider: str
    default_model: str

    async def analyze(
        self,
        *,
        frame_paths: list[Path],
        api_key: str,
        prompt: str,
        model: str | None = None,
    ) -> VisionResult:
        raise NotImplementedError


class TextGenerationClient:
    provider: str
    default_model: str

    async def generate(
        self,
        *,
        system: str,
        user: str,
        api_key: str,
        max_tokens: int = 2048,
        model: str | None = None,
    ) -> GenerationResult:
        raise NotImplementedError
