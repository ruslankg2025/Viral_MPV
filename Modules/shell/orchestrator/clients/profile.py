"""Profile client: получение полного профиля аккаунта для script-gen."""
from typing import Any

import httpx

from orchestrator.logging_setup import get_logger

log = get_logger("clients.profile")


class ProfileError(Exception):
    pass


class ProfileClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Token": self.token}

    async def get_full_profile(self, account_id: str) -> dict[str, Any] | None:
        """Возвращает None если аккаунт не найден (graceful fallback для generate)."""
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(
                f"{self.base_url}/profile/accounts/{account_id}",
                headers=self._headers,
            )
        if r.status_code == 404:
            log.warning("profile_not_found", account_id=account_id)
            return None
        if r.status_code != 200:
            raise ProfileError(
                f"get_profile_failed: {r.status_code} {r.text[:200]}"
            )
        return r.json()
