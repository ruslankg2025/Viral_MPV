import time

import httpx

from .base import GenerationResult, ProviderError, TextGenerationClient


class AnthropicTextClient(TextGenerationClient):
    provider = "anthropic_claude_text"
    default_model = "claude-sonnet-4-6"
    base_url = "https://api.anthropic.com/v1/messages"

    async def generate(
        self,
        *,
        system: str,
        user: str,
        api_key: str,
        max_tokens: int = 2048,
        model: str | None = None,
    ) -> GenerationResult:
        body = {
            "model": model or self.default_model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            r = await client.post(self.base_url, headers=headers, json=body)
        if r.status_code != 200:
            raise ProviderError(f"anthropic_text_failed: {r.status_code} {r.text[:200]}")
        data = r.json()

        text_chunks = [
            b.get("text", "")
            for b in data.get("content", [])
            if b.get("type") == "text"
        ]
        text = "".join(text_chunks).strip()

        usage = data.get("usage", {})
        return GenerationResult(
            text=text,
            provider=self.provider,
            model=data.get("model") or body["model"],
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            latency_ms=int((time.monotonic() - start) * 1000),
        )
