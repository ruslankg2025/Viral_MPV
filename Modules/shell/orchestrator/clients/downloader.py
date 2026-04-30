import asyncio
from typing import Any

import httpx

from orchestrator.logging_setup import get_logger

log = get_logger("clients.downloader")


class DownloaderError(Exception):
    pass


class DownloaderClient:
    def __init__(self, base_url: str, token: str, *, poll_interval_sec: float = 1.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.poll_interval_sec = poll_interval_sec

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Worker-Token": self.token}

    async def submit(
        self,
        *,
        url: str,
        platform: str,
        quality: str = "720p",
        cache_key: str | None = None,
    ) -> str:
        body: dict[str, Any] = {"url": url, "platform": platform, "quality": quality}
        if cache_key:
            body["cache_key"] = cache_key
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.post(
                f"{self.base_url}/jobs/download", json=body, headers=self._headers
            )
        if r.status_code != 202:
            raise DownloaderError(f"submit_failed: {r.status_code} {r.text}")
        return r.json()["job_id"]

    async def get(self, job_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{self.base_url}/jobs/{job_id}", headers=self._headers)
        if r.status_code != 200:
            raise DownloaderError(f"get_failed: {r.status_code} {r.text}")
        return r.json()

    async def wait_done(
        self, job_id: str, *, timeout_sec: int = 300
    ) -> dict[str, Any]:
        """Поллит GET /jobs/{id} до status в (done, failed) или таймаута."""
        deadline = asyncio.get_event_loop().time() + timeout_sec
        while True:
            job = await self.get(job_id)
            status = job.get("status")
            if status == "done":
                return job
            if status == "failed":
                raise DownloaderError(f"job_failed: {job.get('error')}")
            if asyncio.get_event_loop().time() >= deadline:
                raise DownloaderError(f"poll_timeout: {timeout_sec}s")
            await asyncio.sleep(self.poll_interval_sec)

    async def delete_file(self, job_id: str) -> None:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.delete(
                f"{self.base_url}/files/{job_id}", headers=self._headers
            )
        if r.status_code not in (204, 404):
            log.warning(
                "delete_file_unexpected", job_id=job_id, status=r.status_code, body=r.text
            )

    async def healthz(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{self.base_url}/healthz")
        r.raise_for_status()
        return r.json()
