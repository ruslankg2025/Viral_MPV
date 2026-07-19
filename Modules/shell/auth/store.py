"""Хранилище пользователей, сессий и сервисных токенов.

Модель (решение от 2026-07-19): один пользователь — один аккаунт.
`users.account_id` указывает на profile.accounts.id; переключателя
аккаунтов нет, для другого аккаунта — отдельный логин.

Пароли — scrypt из stdlib (bcrypt/passlib в образе нет и тянуть их ради
этого незачем). Сессии серверные: в отличие от JWT их можно отозвать, а
в базе лежит sha256 от выданного токена, чтобы утечка дампа не раздавала
действующие сессии.
"""
import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SESSION_TTL_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id             TEXT PRIMARY KEY,
    username       TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    salt           TEXT NOT NULL,
    account_id     TEXT NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    last_login_at  TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    account_id  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- Сервисные токены для машин (Mentor). Владелец определяется токеном,
-- а не телом запроса, поэтому подделать account_id клиент не может.
CREATE TABLE IF NOT EXISTS api_tokens (
    token_hash    TEXT PRIMARY KEY,
    account_id    TEXT NOT NULL,
    label         TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    last_used_at  TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_password(password: str, salt: str) -> str:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=bytes.fromhex(salt), n=2**14, r=8, p=1
    ).hex()


class AuthStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    # ---------- users ----------
    def create_user(self, *, username: str, password: str, account_id: str) -> str:
        user_id = secrets.token_hex(16)
        salt = os.urandom(16).hex()
        with self._conn() as c:
            c.execute(
                "INSERT INTO users (id, username, password_hash, salt, account_id,"
                " is_active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
                (user_id, username, hash_password(password, salt), salt,
                 account_id, _now()),
            )
        return user_id

    def set_password(self, username: str, password: str) -> bool:
        salt = os.urandom(16).hex()
        with self._conn() as c:
            cur = c.execute(
                "UPDATE users SET password_hash=?, salt=? WHERE username=?",
                (hash_password(password, salt), salt, username),
            )
            return cur.rowcount > 0

    def verify(self, username: str, password: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM users WHERE username=? AND is_active=1", (username,)
            ).fetchone()
        if not row:
            # Считаем хеш и на несуществующем пользователе: иначе по времени
            # ответа видно, какие логины заведены.
            hash_password(password, os.urandom(16).hex())
            return None
        expected = row["password_hash"]
        actual = hash_password(password, row["salt"])
        if not secrets.compare_digest(expected, actual):
            return None
        return dict(row)

    def list_users(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT id, username, account_id, is_active, created_at,"
                " last_login_at FROM users ORDER BY created_at"
            )]

    # ---------- sessions ----------
    def create_session(self, *, user_id: str, account_id: str) -> str:
        raw = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
        with self._conn() as c:
            c.execute(
                "INSERT INTO sessions (token_hash, user_id, account_id, created_at,"
                " expires_at) VALUES (?, ?, ?, ?, ?)",
                (hash_token(raw), user_id, account_id, _now(), expires.isoformat()),
            )
            c.execute("UPDATE users SET last_login_at=? WHERE id=?", (_now(), user_id))
        return raw

    def resolve_session(self, raw_token: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT s.*, u.username FROM sessions s"
                " JOIN users u ON u.id = s.user_id"
                " WHERE s.token_hash=? AND u.is_active=1",
                (hash_token(raw_token),),
            ).fetchone()
        if not row:
            return None
        if row["expires_at"] <= _now():
            self.delete_session(raw_token)
            return None
        return dict(row)

    def delete_session(self, raw_token: str) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM sessions WHERE token_hash=?", (hash_token(raw_token),)
            )
            return cur.rowcount > 0

    def purge_expired_sessions(self) -> int:
        with self._conn() as c:
            return c.execute(
                "DELETE FROM sessions WHERE expires_at <= ?", (_now(),)
            ).rowcount

    # ---------- api tokens ----------
    def create_api_token(self, *, account_id: str, label: str | None = None) -> str:
        raw = "vm_" + secrets.token_urlsafe(32)
        with self._conn() as c:
            c.execute(
                "INSERT INTO api_tokens (token_hash, account_id, label, is_active,"
                " created_at) VALUES (?, ?, ?, 1, ?)",
                (hash_token(raw), account_id, label, _now()),
            )
        return raw

    def resolve_api_token(self, raw_token: str) -> dict[str, Any] | None:
        th = hash_token(raw_token)
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM api_tokens WHERE token_hash=? AND is_active=1", (th,)
            ).fetchone()
            if row:
                c.execute(
                    "UPDATE api_tokens SET last_used_at=? WHERE token_hash=?",
                    (_now(), th),
                )
        return dict(row) if row else None

    def list_api_tokens(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT token_hash, account_id, label, is_active, created_at,"
                " last_used_at FROM api_tokens ORDER BY created_at"
            )]

    def revoke_api_token(self, token_hash: str) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE api_tokens SET is_active=0 WHERE token_hash=?", (token_hash,)
            )
            return cur.rowcount > 0
