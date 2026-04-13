import time
from pathlib import Path

import httpx

from .base import ProviderError, TranscriptionClient, TranscriptResult


class OpenAIWhisperClient(TranscriptionClient):
    provider = "openai_whisper"
    default_model = "whisper-1"
    base_url = "https://api.openai.com/v1/audio/transcriptions"

    async def transcribe(
        self,
        *,
        audio_path: Path,
        api_key: str,
        language: str | None,
        model: str | None = None,
    ) -> TranscriptResult:
        headers = {"Authorization": f"Bearer {api_key}"}
        data = {
            "model": model or self.default_model,
            "response_format": "verbose_json",
        }
        if language and language != "auto":
            data["language"] = language

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
            with audio_path.open("rb") as f:
                files = {"file": (audio_path.name, f, "audio/mpeg")}
                r = await client.post(
                    self.base_url,
                    headers=headers,
                    data=data,
                    files=files,
                )
        if r.status_code != 200:
            raise ProviderError(f"openai_whisper_failed: {r.status_code} {r.text[:200]}")
        body = r.json()
        return TranscriptResult(
            text=body.get("text", ""),
            language=body.get("language"),
            provider=self.provider,
            model=data["model"],
            duration_sec=float(body.get("duration") or 0),
            latency_ms=int((time.monotonic() - start) * 1000),
        )
