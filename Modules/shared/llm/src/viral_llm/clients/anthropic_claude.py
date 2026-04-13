import base64
import json
import re
import time
from pathlib import Path

import httpx

from .base import ProviderError, VisionClient, VisionResult


def _load_image_b64(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    suffix = path.suffix.lower().lstrip(".")
    if suffix in ("jpg", "jpeg"):
        mime = "image/jpeg"
    elif suffix == "png":
        mime = "image/png"
    elif suffix == "webp":
        mime = "image/webp"
    else:
        mime = "image/jpeg"
    return base64.standard_b64encode(data).decode("ascii"), mime


def _extract_json(text: str) -> dict:
    # Попытка 1: чистый JSON
    try:
        return json.loads(text)
    except Exception:
        pass
    # Попытка 2: вытащить из markdown-блока
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # Попытка 3: найти первый { ... }
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    raise ProviderError(f"cannot_parse_vision_json: {text[:200]}")


class AnthropicClaudeClient(VisionClient):
    provider = "anthropic_claude"
    default_model = "claude-sonnet-4-6"
    base_url = "https://api.anthropic.com/v1/messages"

    async def analyze(
        self,
        *,
        frame_paths: list[Path],
        api_key: str,
        prompt: str,
        model: str | None = None,
    ) -> VisionResult:
        content: list[dict] = []
        for i, fp in enumerate(frame_paths, start=1):
            b64, mime = _load_image_b64(fp)
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": b64},
            })
            content.append({"type": "text", "text": f"frame_{i}"})
        content.append({"type": "text", "text": prompt})

        body = {
            "model": model or self.default_model,
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": content}],
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=30.0)) as client:
            r = await client.post(self.base_url, headers=headers, json=body)
        if r.status_code != 200:
            raise ProviderError(f"anthropic_failed: {r.status_code} {r.text[:200]}")
        data = r.json()

        # Извлекаем текст из content (массив блоков)
        text_chunks = [
            b.get("text", "")
            for b in data.get("content", [])
            if b.get("type") == "text"
        ]
        raw_text = "".join(text_chunks).strip()
        parsed = _extract_json(raw_text)

        usage = data.get("usage", {})
        return VisionResult(
            raw_json=parsed,
            provider=self.provider,
            model=data.get("model") or body["model"],
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            latency_ms=int((time.monotonic() - start) * 1000),
        )
