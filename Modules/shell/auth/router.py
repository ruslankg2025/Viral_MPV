"""Ручки входа: /api/auth/login, /logout, /me."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from auth.deps import SESSION_COOKIE, AuthContext, require_auth
from auth.store import SESSION_TTL_DAYS
from orchestrator.logging_setup import get_logger
from orchestrator.state import state

log = get_logger("auth.router")

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginReq(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class MeResp(BaseModel):
    username: str | None
    account_id: str
    account_name: str | None = None
    service: bool = False


@router.post("/login")
async def login(req: LoginReq, response: Response):
    user = state.auth_store.verify(req.username, req.password)
    if not user:
        # Одинаковый ответ и на несуществующий логин, и на неверный пароль —
        # чтобы нельзя было перебором узнать, какие учётки заведены.
        log.warning("login_failed", username=req.username[:64])
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials"
        )
    token = state.auth_store.create_session(
        user_id=user["id"], account_id=user["account_id"]
    )
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,      # недоступна из JS — защита от кражи через XSS
        secure=True,        # только по HTTPS
        samesite="lax",
        max_age=SESSION_TTL_DAYS * 24 * 3600,
        path="/",
    )
    log.info("login_ok", username=req.username, account_id=user["account_id"])
    return {"ok": True, "account_id": user["account_id"], "username": user["username"]}


@router.post("/logout")
async def logout(request: Request, response: Response):
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        state.auth_store.delete_session(raw)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=MeResp)
async def me(auth: AuthContext = Depends(require_auth)):  # noqa: B008
    name = None
    if state.profile_client is not None:
        try:
            prof = await state.profile_client.get_full_profile(auth.account_id) or {}
            acc = prof.get("account") if isinstance(prof.get("account"), dict) else prof
            name = (acc or {}).get("name")
        except Exception as e:  # noqa: BLE001
            log.warning("me_account_lookup_failed", error=str(e))
    return MeResp(
        username=auth.username,
        account_id=auth.account_id,
        account_name=name,
        service=auth.service,
    )
