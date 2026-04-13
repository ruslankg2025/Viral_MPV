import time

import httpx

from .base import GenerationResult, ProviderError, TextGenerationClient


class OpenAITextClient(TextGenerationClient):
    provider = "openai_gpt4o_text"
    default_model = "gpt-4o"
    base_url = "https://api.openai.com/v1/chat/completions"

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
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            r = await client.post(self.base_url, headers=headers, json=body)
        if r.status_code != 200:
            raise ProviderError(f"openai_text_failed: {r.status_code} {r.text[:200]}")
        data = r.json()

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise ProviderError(f"openai_text_bad_response: {e}") from e

        usage = data.get("usage", {})
        return GenerationResult(
            text=content or "",
            provider=self.provider,
            model=data.get("model") or body["model"],
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=int((time.monotonic() - start) * 1000),
        )
