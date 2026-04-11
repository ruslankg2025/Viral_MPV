import base64
import time
from pathlib import Path

import httpx

from clients.anthropic_claude import _extract_json
from clients.base import ProviderError, VisionClient, VisionResult


class GoogleGeminiClient(VisionClient):
    def __init__(self, model: str = "gemini-2.5-pro"):
        self._model = model
        if "flash" in model:
            self.provider = "google_gemini_flash"
            self.default_model = "gemini-2.5-flash"
        else:
            self.provider = "google_gemini_pro"
            self.default_model = "gemini-2.5-pro"

    def _endpoint(self, api_key: str, model: str) -> str:
        return (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )

    async def analyze(
        self,
        *,
        frame_paths: list[Path],
        api_key: str,
        prompt: str,
        model: str | None = None,
    ) -> VisionResult:
        parts: list[dict] = []
        for i, fp in enumerate(frame_paths, start=1):
            data = fp.read_bytes()
            mime = "image/jpeg"
            parts.append({"text": f"frame_{i}"})
            parts.append(
                {
                    "inline_data": {
                        "mime_type": mime,
                        "data": base64.standard_b64encode(data).decode("ascii"),
                    }
                }
            )
        parts.append({"text": prompt})

        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "maxOutputTokens": 2048,
                "responseMimeType": "application/json",
            },
        }

        actual_model = model or self.default_model
        url = self._endpoint(api_key, actual_model)
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=30.0)) as client:
            r = await client.post(url, json=body)
        if r.status_code != 200:
            raise ProviderError(f"{self.provider}_failed: {r.status_code} {r.text[:200]}")
        data = r.json()

        try:
            candidate = data["candidates"][0]
            text_parts = candidate["content"]["parts"]
            raw_text = "".join(p.get("text", "") for p in text_parts)
        except (KeyError, IndexError) as e:
            raise ProviderError(f"{self.provider}_bad_response: {e}") from e
        parsed = _extract_json(raw_text or "{}")

        usage = data.get("usageMetadata", {})
        return VisionResult(
            raw_json=parsed,
            provider=self.provider,
            model=actual_model,
            input_tokens=int(usage.get("promptTokenCount", 0)),
            output_tokens=int(usage.get("candidatesTokenCount", 0)),
            latency_ms=int((time.monotonic() - start) * 1000),
        )
