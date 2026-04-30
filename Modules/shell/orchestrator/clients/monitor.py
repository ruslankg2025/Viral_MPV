"""Monitor client: lookup metadata видео по video_id и запись результата
analyze-pipeline через PATCH (Этап 5)."""
from typing import Any

import httpx

from orchestrator.logging_setup import get_logger

log = get_logger("clients.monitor")


class MonitorError(Exception):
    pass


class MonitorClient:
    def __init__(self, base_url: str, token: str):
        # base_url из env у нас уже без /monitor (http://monitor:8000),
        # endpoints мы знаем (/monitor/videos/...)
        self.base_url = base_url.rstrip("/")
        self.token = token

    @property
    def _headers(self) -> dict[str, str]:
        # monitor использует X-Token (см. monitor/auth.py)
        return {"X-Token": self.token}

    async def get_video(self, video_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(
                f"{self.base_url}/monitor/videos/{video_id}",
                headers=self._headers,
            )
        if r.status_code == 404:
            raise MonitorError(f"video_not_found: {video_id}")
        if r.status_code != 200:
            raise MonitorError(f"get_video_failed: {r.status_code} {r.text[:200]}")
        return r.json()

    async def patch_analysis(
        self,
        video_id: str,
        *,
        orchestrator_run_id: str | None = None,
        script_id: str | None = None,
        sha256: str | None = None,
        analysis_done_at: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if orchestrator_run_id is not None:
            body["orchestrator_run_id"] = orchestrator_run_id
        if script_id is not None:
            body["script_id"] = script_id
        if sha256 is not None:
            body["sha256"] = sha256
        if analysis_done_at is not None:
            body["analysis_done_at"] = analysis_done_at

        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.patch(
                f"{self.base_url}/monitor/videos/{video_id}/analysis",
                json=body,
                headers=self._headers,
            )
        if r.status_code != 200:
            raise MonitorError(
                f"patch_analysis_failed: {r.status_code} {r.text[:200]}"
            )
        return r.json()
