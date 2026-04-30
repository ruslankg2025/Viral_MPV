"""Script client: генерация сценария через script-сервис."""
from typing import Any

import httpx

from orchestrator.logging_setup import get_logger

log = get_logger("clients.script")


class ScriptError(Exception):
    pass


class ScriptClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Worker-Token": self.token}

    async def generate(
        self,
        *,
        template: str,
        params: dict[str, Any],
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        """POST /script/generate → dict с полем 'id' (script_id).

        Таймаут 120 сек: LLM-генерация может занять до 60 сек.
        """
        body = {"template": template, "params": params, "profile": profile}
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.post(
                f"{self.base_url}/script/generate",
                json=body,
                headers=self._headers,
            )
        if r.status_code != 201:
            raise ScriptError(
                f"generate_failed: {r.status_code} {r.text[:300]}"
            )
        return r.json()
