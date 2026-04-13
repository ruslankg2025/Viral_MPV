import base64
import time
from pathlib import Path

import httpx

from .anthropic_claude import _extract_json
from .base import ProviderError, VisionClient, VisionResult


def _data_url(path: Path) -> str:
    data = path.read_bytes()
    suffix = path.suffix.lower().lstrip(".")
    mime = "image/jpeg" if suffix in ("jpg", "jpeg") else f"image/{suffix}"
    return f"data:{mime};base64,{base64.standard_b64encode(data).decode('ascii')}"


class _OpenAIBase(VisionClient):
    base_url = "https://api.openai.com/v1/chat/completions"

    async def analyze(
        self,
        *,
        frame_paths: list[Path],
        api_key: str,
        prompt: str,
        model: str | None = None,
    ) -> VisionResult:
        user_content: list[dict] = []
        for i, fp in enumerate(frame_paths, start=1):
            user_content.append({"type": "text", "text": f"frame_{i}"})
            user_content.append(
                {"type": "image_url", "image_url": {"url": _data_url(fp), "detail": "low"}}
            )
        user_content.append({"type": "text", "text": prompt})

        body = {
            "model": model or self.default_model,
            "messages": [{"role": "user", "content": user_content}],
            "max_tokens": 2048,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=30.0)) as client:
            r = await client.post(self.base_url, headers=headers, json=body)
        if r.status_code != 200:
            raise ProviderError(f"{self.provider}_failed: {r.status_code} {r.text[:200]}")
        data = r.json()

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise ProviderError(f"{self.provider}_bad_response: {e}") from e
        parsed = _extract_json(content or "{}")

        usage = data.get("usage", {})
        return VisionResult(
            raw_json=parsed,
            provider=self.provider,
            model=data.get("model") or body["model"],
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=int((time.monotonic() - start) * 1000),
        )


class OpenAIGPT4oClient(_OpenAIBase):
    provider = "openai_gpt4o"
    default_model = "gpt-4o"


class OpenAIGPT4oMiniClient(_OpenAIBase):
    provider = "openai_gpt4o_mini"
    default_model = "gpt-4o-mini"
