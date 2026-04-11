import asyncio
import time
from pathlib import Path

import httpx

from clients.base import ProviderError, TranscriptionClient, TranscriptResult


class AssemblyAIClient(TranscriptionClient):
    provider = "assemblyai"
    default_model = "best"
    base_url = "https://api.assemblyai.com/v2"

    async def transcribe(
        self,
        *,
        audio_path: Path,
        api_key: str,
        language: str | None,
        model: str | None = None,
    ) -> TranscriptResult:
        headers = {"authorization": api_key}
        start = time.monotonic()

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
            # 1. Загрузка файла
            with audio_path.open("rb") as f:
                upload = await client.post(
                    f"{self.base_url}/upload",
                    headers=headers,
                    content=f.read(),
                )
            if upload.status_code != 200:
                raise ProviderError(f"upload_failed: {upload.status_code} {upload.text[:200]}")
            upload_url = upload.json()["upload_url"]

            # 2. Создание транскрипции
            body = {
                "audio_url": upload_url,
                "speech_model": model or self.default_model,
            }
            if language and language != "auto":
                body["language_code"] = language
            else:
                body["language_detection"] = True

            create = await client.post(
                f"{self.base_url}/transcript", headers=headers, json=body
            )
            if create.status_code not in (200, 201):
                raise ProviderError(
                    f"create_failed: {create.status_code} {create.text[:200]}"
                )
            transcript_id = create.json()["id"]

            # 3. Поллинг до завершения
            for _ in range(300):  # до 5 минут
                await asyncio.sleep(1.0)
                poll = await client.get(
                    f"{self.base_url}/transcript/{transcript_id}", headers=headers
                )
                if poll.status_code != 200:
                    raise ProviderError(f"poll_failed: {poll.status_code}")
                data = poll.json()
                status_val = data.get("status")
                if status_val == "completed":
                    return TranscriptResult(
                        text=data.get("text", ""),
                        language=data.get("language_code"),
                        provider=self.provider,
                        model=body["speech_model"],
                        duration_sec=float(data.get("audio_duration") or 0),
                        latency_ms=int((time.monotonic() - start) * 1000),
                    )
                if status_val == "error":
                    raise ProviderError(f"assemblyai_error: {data.get('error')}")

            raise ProviderError("assemblyai_timeout")
