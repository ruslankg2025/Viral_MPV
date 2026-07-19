"""Зависимость аутентификации: сессия в куке или сервисный токен.

Владелец (account_id) берётся ТОЛЬКО отсюда. Ручки не должны читать
account_id из тела или query — иначе любой клиент подставит чужой и
получит доступ к его разборам.
"""
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status

from orchestrator.state import state

SESSION_COOKIE = "vira_session"


@dataclass(frozen=True)
class AuthContext:
    account_id: str
    username: str | None = None
    # service=True — пришли по сервисному токену (Mentor), не по сессии.
    service: bool = False
    # enforce=False — режим до Фазы 2: владельца не проверяем и список не
    # фильтруем. У старых runs account_id = None, и фильтрация по любому
    # значению вернула бы пустую студию.
    enforce: bool = True


async def require_auth(
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> AuthContext:
    store = getattr(state, "auth_store", None)

    # Сервисный токен резолвим ВСЕГДА, независимо от AUTH_ENABLED. Раньше
    # проверка флага стояла выше и перехватывала запрос до неё — Mentor
    # приходил с валидным X-Api-Key, а run уезжал на __legacy__ вместо
    # своего аккаунта. Владение здесь всегда строгое (enforce=True):
    # машина видит только то, что принадлежит её аккаунту.
    if x_api_key:
        if store is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, detail="auth_not_initialized"
            )
        tok = store.resolve_api_token(x_api_key)
        if tok:
            return AuthContext(
                account_id=tok["account_id"], service=True, enforce=True
            )
        # Неверный токен — 401 даже при выключенном флаге: тот, кто пришёл
        # с токеном, не должен молча становиться legacy-владельцем.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid_api_key")

    # Сессионный вход — за флагом, до появления экрана входа в проде.
    settings = getattr(state, "settings", None)
    if settings is not None and not getattr(settings, "auth_enabled", False):
        return AuthContext(
            account_id=settings.auth_default_account_id, enforce=False
        )

    if store is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="auth_not_initialized"
        )

    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        sess = store.resolve_session(raw)
        if sess:
            return AuthContext(
                account_id=sess["account_id"], username=sess["username"]
            )

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="not_authenticated")


async def optional_auth(
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> AuthContext | None:
    """Для ручек, которые пока нельзя закрывать жёстко (переходный период)."""
    try:
        return await require_auth(request, x_api_key)
    except HTTPException:
        return None


CurrentAuth = Depends(require_auth)
