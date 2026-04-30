import time
from pathlib import Path

import httpx

from .base import ProviderError, TranscriptionClient, TranscriptResult


class DeepgramClient(TranscriptionClient):
    provider = "deepgram"
    default_model = "nova-3"
    base_url = "https://api.deepgram.com/v1/listen"

    async def transcribe(
        self,
        *,
        audio_path: Path,
        api_key: str,
        language: str | None,
        model: str | None = None,
    ) -> TranscriptResult:
        headers = {
            "Authorization": f"Token {api_key}",
            "Content-Type": "audio/mpeg",
        }
        params: dict[str, str] = {
            "model": model or self.default_model,
            "smart_format": "true",
            "punctuate": "true",
            "utterances": "true",
        }
        if language and language != "auto":
            params["language"] = language
        else:
            params["detect_language"] = "true"

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
            with audio_path.open("rb") as f:
                r = await client.post(
                    self.base_url,
                    headers=headers,
                    params=params,
                    content=f.read(),
                )
        if r.status_code != 200:
            raise ProviderError(f"deepgram_failed: {r.status_code} {r.text[:200]}")
        data = r.json()
        try:
            alt = data["results"]["channels"][0]["alternatives"][0]
            text = alt.get("transcript", "")
        except (KeyError, IndexError) as e:
            raise ProviderError(f"deepgram_bad_response: {e}") from e

        duration = float(data.get("metadata", {}).get("duration") or 0)
        detected_lang = (
            data["results"]["channels"][0].get("detected_language")
            or params.get("language")
        )
        segments = [
            {"start": float(u.get("start") or 0), "end": float(u.get("end") or 0), "text": (u.get("transcript") or "").strip()}
            for u in (data.get("results", {}).get("utterances") or [])
        ]

        return TranscriptResult(
            text=text,
            language=detected_lang,
            provider=self.provider,
            model=params["model"],
            duration_sec=duration,
            latency_ms=int((time.monotonic() - start) * 1000),
            segments=segments,
        )
