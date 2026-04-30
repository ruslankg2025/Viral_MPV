import asyncio
from typing import Any

import httpx

from orchestrator.logging_setup import get_logger

log = get_logger("clients.processor")


class ProcessorError(Exception):
    pass


class ProcessorClient:
    def __init__(self, base_url: str, token: str, *, poll_interval_sec: float = 1.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.poll_interval_sec = poll_interval_sec

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Worker-Token": self.token}

    async def _submit(self, endpoint: str, body: dict[str, Any]) -> str:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(
                f"{self.base_url}/jobs/{endpoint}", json=body, headers=self._headers
            )
        if r.status_code != 202:
            raise ProcessorError(f"submit_failed:{endpoint}: {r.status_code} {r.text}")
        return r.json()["job_id"]

    async def submit_transcribe(
        self,
        *,
        file_path: str,
        cache_key: str | None = None,
        source_ref: dict[str, str] | None = None,
    ) -> str:
        body: dict[str, Any] = {"file_path": file_path}
        if cache_key:
            body["cache_key"] = cache_key
        if source_ref:
            body["source_ref"] = source_ref
        return await self._submit("transcribe", body)

    async def submit_vision_analyze(
        self,
        *,
        file_path: str,
        cache_key: str | None = None,
        source_ref: dict[str, str] | None = None,
    ) -> str:
        body: dict[str, Any] = {"file_path": file_path}
        if cache_key:
            body["cache_key"] = cache_key
        if source_ref:
            body["source_ref"] = source_ref
        return await self._submit("vision-analyze", body)

    async def submit_full_analysis(
        self,
        *,
        file_path: str,
        cache_key: str | None = None,
        source_ref: dict[str, str] | None = None,
    ) -> str:
        body: dict[str, Any] = {"file_path": file_path}
        if cache_key:
            body["cache_key"] = cache_key
        if source_ref:
            body["source_ref"] = source_ref
        return await self._submit("full-analysis", body)

    async def submit_analyze_strategy(
        self,
        *,
        transcript_text: str,
        vision_analysis: dict[str, Any] | None = None,
        cache_key: str | None = None,
        source_ref: dict[str, str] | None = None,
    ) -> str:
        body: dict[str, Any] = {"transcript_text": transcript_text}
        if vision_analysis:
            body["vision_analysis"] = vision_analysis
        if cache_key:
            body["cache_key"] = cache_key
        if source_ref:
            body["source_ref"] = source_ref
        return await self._submit("analyze-strategy", body)

    async def get(self, job_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{self.base_url}/jobs/{job_id}", headers=self._headers)
        if r.status_code != 200:
            raise ProcessorError(f"get_failed: {r.status_code} {r.text}")
        return r.json()

    async def wait_done(
        self, job_id: str, *, timeout_sec: int = 300
    ) -> dict[str, Any]:
        deadline = asyncio.get_event_loop().time() + timeout_sec
        while True:
            job = await self.get(job_id)
            status = job.get("status")
            if status == "done":
                return job
            if status == "failed":
                raise ProcessorError(f"job_failed: {job.get('error')}")
            if asyncio.get_event_loop().time() >= deadline:
                raise ProcessorError(f"poll_timeout: {timeout_sec}s")
            await asyncio.sleep(self.poll_interval_sec)

    async def healthz(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{self.base_url}/healthz")
        r.raise_for_status()
        return r.json()
