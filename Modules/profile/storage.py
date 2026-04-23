"""
ProfileStore — SQLite-хранилище для всех блоков профиля аккаунта.

Таблицы:
  accounts          — реестр аккаунтов (+ language, niche_slugs_json для multi-niche)
  niche_taxonomy    — справочник ниш (seed из fixtures/taxonomy.json); поле type: mass/expert/both
  brand_books       — tone-of-voice (axes + tone_preset), запрещённые слова, CTA
  audience_profiles — демография и боли ЦА
  prompt_profiles   — системные промпты с версионированием
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    niche_slug       TEXT,
    niche_slugs_json TEXT NOT NULL DEFAULT '[]',
    language         TEXT NOT NULL DEFAULT 'ru',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS niche_taxonomy (
    slug        TEXT PRIMARY KEY,
    label_ru    TEXT NOT NULL,
    label_en    TEXT,
    parent_slug TEXT REFERENCES niche_taxonomy(slug),
    type        TEXT
);

CREATE TABLE IF NOT EXISTS brand_books (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id           TEXT NOT NULL UNIQUE REFERENCES accounts(id),
    tone_preset          TEXT,
    formality            INTEGER,
    energy               INTEGER,
    humor                INTEGER,
    expertise            INTEGER,
    forbidden_words_json TEXT NOT NULL DEFAULT '[]',
    cta_json             TEXT NOT NULL DEFAULT '[]',
    extra_json           TEXT NOT NULL DEFAULT '{}',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audience_profiles (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id        TEXT NOT NULL UNIQUE REFERENCES accounts(id),
    age_range         TEXT,
    geography         TEXT,
    gender            TEXT,
    expertise_level   TEXT,
    pain_points_json  TEXT NOT NULL DEFAULT '[]',
    desires_json      TEXT NOT NULL DEFAULT '[]',
    extra_json        TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_profiles (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id            TEXT NOT NULL REFERENCES accounts(id),
    version               TEXT NOT NULL,
    system_prompt         TEXT NOT NULL,
    modifiers_json        TEXT NOT NULL DEFAULT '{}',
    hard_constraints_json TEXT NOT NULL DEFAULT '{}',
    soft_constraints_json TEXT NOT NULL DEFAULT '{}',
    is_active             INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT NOT NULL,
    UNIQUE(account_id, version)
);
"""


# Идемпотентные миграции для уже существующих БД
# (SCHEMA CREATE TABLE IF NOT EXISTS не добавляет колонки в существующие таблицы)
_MIGRATIONS: list[tuple[str, str]] = [
    ("accounts",       "ALTER TABLE accounts ADD COLUMN niche_slugs_json TEXT NOT NULL DEFAULT '[]'"),
    ("accounts",       "ALTER TABLE accounts ADD COLUMN language TEXT NOT NULL DEFAULT 'ru'"),
    ("niche_taxonomy", "ALTER TABLE niche_taxonomy ADD COLUMN type TEXT"),
    ("brand_books",    "ALTER TABLE brand_books ADD COLUMN tone_preset TEXT"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AccountRow:
    id: str
    name: str
    niche_slug: str | None
    created_at: str
    updated_at: str
    niche_slugs: list[str] = field(default_factory=list)
    language: str = "ru"


@dataclass
class BrandBookRow:
    id: int
    account_id: str
    tone_preset: str | None
    formality: int | None
    energy: int | None
    humor: int | None
    expertise: int | None
    forbidden_words: list[str]
    cta: list[str]
    extra: dict
    created_at: str
    updated_at: str


@dataclass
class AudienceRow:
    id: int
    account_id: str
    age_range: str | None
    geography: str | None
    gender: str | None
    expertise_level: str | None
    pain_points: list[str]
    desires: list[str]
    extra: dict
    created_at: str
    updated_at: str


@dataclass
class PromptProfileRow:
    id: int
    account_id: str
    version: str
    system_prompt: str
    modifiers: dict
    hard_constraints: dict
    soft_constraints: dict
    is_active: bool
    created_at: str


class ProfileStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            self._migrate(c)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @staticmethod
    def _migrate(c: sqlite3.Connection) -> None:
        """Идемпотентно добавить новые колонки в существующие БД."""
        for table, stmt in _MIGRATIONS:
            col = stmt.split("ADD COLUMN", 1)[1].strip().split()[0]
            info = c.execute(f"PRAGMA table_info({table})").fetchall()
            if not any(r["name"] == col for r in info):
                c.execute(stmt)

    # ------------------------------------------------------------------ #
    # Niche Taxonomy
    # ------------------------------------------------------------------ #

    def seed_taxonomy(self, entries: list[dict]) -> int:
        """Загрузить ниши из списка [{slug, label_ru, label_en?, parent_slug?, type?}].
        Пропускает уже существующие (upsert по slug).
        Возвращает количество добавленных записей.
        """
        added = 0
        with self._conn() as c:
            for e in entries:
                cur = c.execute(
                    "INSERT OR IGNORE INTO niche_taxonomy (slug, label_ru, label_en, parent_slug, type)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (e["slug"], e["label_ru"], e.get("label_en"), e.get("parent_slug"), e.get("type")),
                )
                added += cur.rowcount
        return added

    def list_taxonomy(self, parent_slug: str | None = None) -> list[dict]:
        with self._conn() as c:
            if parent_slug is None:
                rows = c.execute(
                    "SELECT slug, label_ru, label_en, parent_slug, type FROM niche_taxonomy ORDER BY slug"
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT slug, label_ru, label_en, parent_slug, type FROM niche_taxonomy"
                    " WHERE parent_slug = ? ORDER BY slug",
                    (parent_slug,),
                ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Accounts
    # ------------------------------------------------------------------ #

    @staticmethod
    def _sync_main_niche(niche_slugs: list[str] | None) -> tuple[str | None, list[str]]:
        """niche_slug (главная) = niche_slugs[0] если список непуст."""
        slugs = list(niche_slugs or [])
        main = slugs[0] if slugs else None
        return main, slugs

    def create_account_with_id(
        self,
        account_id: str,
        name: str,
        niche_slug: str | None = None,
        niche_slugs: list[str] | None = None,
        language: str = "ru",
    ) -> AccountRow:
        """Создать аккаунт с явным id (для seed/fixtures)."""
        now = _now()
        # Если передан только niche_slug — заполняем niche_slugs
        if niche_slugs is None and niche_slug is not None:
            niche_slugs = [niche_slug]
        main, slugs = self._sync_main_niche(niche_slugs)
        with self._conn() as c:
            c.execute(
                "INSERT INTO accounts (id, name, niche_slug, niche_slugs_json, language,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (account_id, name, main, json.dumps(slugs, ensure_ascii=False), language, now, now),
            )
        return AccountRow(
            id=account_id, name=name, niche_slug=main, niche_slugs=slugs, language=language,
            created_at=now, updated_at=now,
        )

    def create_account(
        self,
        name: str,
        niche_slug: str | None = None,
        niche_slugs: list[str] | None = None,
        language: str = "ru",
    ) -> AccountRow:
        return self.create_account_with_id(
            str(uuid4()), name,
            niche_slug=niche_slug, niche_slugs=niche_slugs, language=language,
        )

    def get_account(self, account_id: str) -> AccountRow | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            return None
        return self._account_row(dict(row))

    def list_accounts(self) -> list[AccountRow]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM accounts ORDER BY created_at DESC").fetchall()
        return [self._account_row(dict(r)) for r in rows]

    def update_account(
        self,
        account_id: str,
        name: str | None = None,
        niche_slug: str | None = None,
        niche_slugs: list[str] | None = None,
        language: str | None = None,
    ) -> bool:
        parts: list[str] = []
        vals: list = []
        if name is not None:
            parts.append("name = ?")
            vals.append(name)

        # niche_slugs имеет приоритет над niche_slug: если передан список — заменяем целиком
        if niche_slugs is not None:
            main, slugs = self._sync_main_niche(niche_slugs)
            parts.extend(["niche_slug = ?", "niche_slugs_json = ?"])
            vals.extend([main, json.dumps(slugs, ensure_ascii=False)])
        elif niche_slug is not None:
            # Совместимость: singular → обновляем оба поля, niche_slugs = [niche_slug]
            parts.extend(["niche_slug = ?", "niche_slugs_json = ?"])
            vals.extend([niche_slug, json.dumps([niche_slug], ensure_ascii=False)])

        if language is not None:
            parts.append("language = ?")
            vals.append(language)

        if not parts:
            return False
        parts.append("updated_at = ?")
        vals.append(_now())
        vals.append(account_id)
        with self._conn() as c:
            cur = c.execute(f"UPDATE accounts SET {', '.join(parts)} WHERE id = ?", vals)
        return cur.rowcount > 0

    def delete_account(self, account_id: str) -> bool:
        """Удалить аккаунт + каскад: brand_book, audience, prompt_profiles.
        Возвращает True если аккаунт существовал.
        """
        with self._conn() as c:
            exists = c.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone()
            if exists is None:
                return False
            # Ручной каскад — REFERENCES без ON DELETE CASCADE
            c.execute("DELETE FROM prompt_profiles WHERE account_id = ?", (account_id,))
            c.execute("DELETE FROM audience_profiles WHERE account_id = ?", (account_id,))
            c.execute("DELETE FROM brand_books WHERE account_id = ?", (account_id,))
            c.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        return True

    @staticmethod
    def _account_row(d: dict) -> AccountRow:
        raw = d.get("niche_slugs_json") or "[]"
        try:
            slugs = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            slugs = []
        return AccountRow(
            id=d["id"], name=d["name"],
            niche_slug=d.get("niche_slug"),
            niche_slugs=slugs,
            language=d.get("language") or "ru",
            created_at=d["created_at"], updated_at=d["updated_at"],
        )

    # ------------------------------------------------------------------ #
    # Brand Book (один на аккаунт; upsert)
    # ------------------------------------------------------------------ #

    def upsert_brand_book(
        self,
        account_id: str,
        tone_preset: str | None = None,
        formality: int | None = None,
        energy: int | None = None,
        humor: int | None = None,
        expertise: int | None = None,
        forbidden_words: list[str] | None = None,
        cta: list[str] | None = None,
        extra: dict | None = None,
    ) -> BrandBookRow:
        now = _now()
        with self._conn() as c:
            existing = c.execute(
                "SELECT * FROM brand_books WHERE account_id = ?", (account_id,)
            ).fetchone()
            if existing:
                # merge: None → keep existing
                row = dict(existing)
                tp = tone_preset if tone_preset is not None else row.get("tone_preset")
                f = formality if formality is not None else row["formality"]
                en = energy if energy is not None else row["energy"]
                hu = humor if humor is not None else row["humor"]
                ex = expertise if expertise is not None else row["expertise"]
                fw = json.dumps(forbidden_words, ensure_ascii=False) if forbidden_words is not None else row["forbidden_words_json"]
                ct = json.dumps(cta, ensure_ascii=False) if cta is not None else row["cta_json"]
                ex2 = json.dumps(extra, ensure_ascii=False) if extra is not None else row["extra_json"]
                c.execute(
                    "UPDATE brand_books SET tone_preset=?, formality=?, energy=?, humor=?, expertise=?,"
                    " forbidden_words_json=?, cta_json=?, extra_json=?, updated_at=?"
                    " WHERE account_id=?",
                    (tp, f, en, hu, ex, fw, ct, ex2, now, account_id),
                )
                bid = row["id"]
                created_at = row["created_at"]
                return BrandBookRow(
                    id=bid, account_id=account_id, tone_preset=tp,
                    formality=f, energy=en, humor=hu, expertise=ex,
                    forbidden_words=json.loads(fw), cta=json.loads(ct), extra=json.loads(ex2),
                    created_at=created_at, updated_at=now,
                )
            else:
                fw = json.dumps(forbidden_words or [], ensure_ascii=False)
                ct = json.dumps(cta or [], ensure_ascii=False)
                ex2 = json.dumps(extra or {}, ensure_ascii=False)
                cur = c.execute(
                    "INSERT INTO brand_books (account_id, tone_preset, formality, energy, humor, expertise,"
                    " forbidden_words_json, cta_json, extra_json, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (account_id, tone_preset, formality, energy, humor, expertise, fw, ct, ex2, now, now),
                )
                return BrandBookRow(
                    id=cur.lastrowid, account_id=account_id, tone_preset=tone_preset,
                    formality=formality, energy=energy, humor=humor, expertise=expertise,
                    forbidden_words=forbidden_words or [], cta=cta or [], extra=extra or {},
                    created_at=now, updated_at=now,
                )

    def get_brand_book(self, account_id: str) -> BrandBookRow | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM brand_books WHERE account_id = ?", (account_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        return BrandBookRow(
            id=d["id"], account_id=d["account_id"],
            tone_preset=d.get("tone_preset"),
            formality=d["formality"], energy=d["energy"],
            humor=d["humor"], expertise=d["expertise"],
            forbidden_words=json.loads(d["forbidden_words_json"]),
            cta=json.loads(d["cta_json"]),
            extra=json.loads(d["extra_json"]),
            created_at=d["created_at"], updated_at=d["updated_at"],
        )

    # ------------------------------------------------------------------ #
    # Audience Profile (один на аккаунт; upsert)
    # ------------------------------------------------------------------ #

    def upsert_audience(
        self,
        account_id: str,
        age_range: str | None = None,
        geography: str | None = None,
        gender: str | None = None,
        expertise_level: str | None = None,
        pain_points: list[str] | None = None,
        desires: list[str] | None = None,
        extra: dict | None = None,
    ) -> AudienceRow:
        now = _now()
        with self._conn() as c:
            existing = c.execute(
                "SELECT * FROM audience_profiles WHERE account_id = ?", (account_id,)
            ).fetchone()
            if existing:
                row = dict(existing)
                ar = age_range if age_range is not None else row["age_range"]
                geo = geography if geography is not None else row["geography"]
                gen = gender if gender is not None else row["gender"]
                el = expertise_level if expertise_level is not None else row["expertise_level"]
                pp = json.dumps(pain_points, ensure_ascii=False) if pain_points is not None else row["pain_points_json"]
                des = json.dumps(desires, ensure_ascii=False) if desires is not None else row["desires_json"]
                ex2 = json.dumps(extra, ensure_ascii=False) if extra is not None else row["extra_json"]
                c.execute(
                    "UPDATE audience_profiles SET age_range=?, geography=?, gender=?,"
                    " expertise_level=?, pain_points_json=?, desires_json=?, extra_json=?, updated_at=?"
                    " WHERE account_id=?",
                    (ar, geo, gen, el, pp, des, ex2, now, account_id),
                )
                aid2 = row["id"]
                created_at = row["created_at"]
                return AudienceRow(
                    id=aid2, account_id=account_id,
                    age_range=ar, geography=geo, gender=gen, expertise_level=el,
                    pain_points=json.loads(pp), desires=json.loads(des), extra=json.loads(ex2),
                    created_at=created_at, updated_at=now,
                )
            else:
                pp = json.dumps(pain_points or [], ensure_ascii=False)
                des = json.dumps(desires or [], ensure_ascii=False)
                ex2 = json.dumps(extra or {}, ensure_ascii=False)
                cur = c.execute(
                    "INSERT INTO audience_profiles (account_id, age_range, geography, gender,"
                    " expertise_level, pain_points_json, desires_json, extra_json, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (account_id, age_range, geography, gender, expertise_level, pp, des, ex2, now, now),
                )
                return AudienceRow(
                    id=cur.lastrowid, account_id=account_id,
                    age_range=age_range, geography=geography, gender=gender, expertise_level=expertise_level,
                    pain_points=pain_points or [], desires=desires or [], extra=extra or {},
                    created_at=now, updated_at=now,
                )

    def get_audience(self, account_id: str) -> AudienceRow | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM audience_profiles WHERE account_id = ?", (account_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        return AudienceRow(
            id=d["id"], account_id=d["account_id"],
            age_range=d["age_range"], geography=d["geography"],
            gender=d["gender"], expertise_level=d["expertise_level"],
            pain_points=json.loads(d["pain_points_json"]),
            desires=json.loads(d["desires_json"]),
            extra=json.loads(d["extra_json"]),
            created_at=d["created_at"], updated_at=d["updated_at"],
        )

    # ------------------------------------------------------------------ #
    # Prompt Profile (версионированный; одна активная версия)
    # ------------------------------------------------------------------ #

    def create_prompt_profile(
        self,
        account_id: str,
        version: str,
        system_prompt: str,
        modifiers: dict | None = None,
        hard_constraints: dict | None = None,
        soft_constraints: dict | None = None,
    ) -> PromptProfileRow:
        now = _now()
        mod = json.dumps(modifiers or {}, ensure_ascii=False)
        hc = json.dumps(hard_constraints or {}, ensure_ascii=False)
        sc = json.dumps(soft_constraints or {}, ensure_ascii=False)
        with self._conn() as c:
            # деактивировать предыдущие
            c.execute(
                "UPDATE prompt_profiles SET is_active = 0 WHERE account_id = ?", (account_id,)
            )
            cur = c.execute(
                "INSERT INTO prompt_profiles (account_id, version, system_prompt,"
                " modifiers_json, hard_constraints_json, soft_constraints_json, is_active, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (account_id, version, system_prompt, mod, hc, sc, now),
            )
        return PromptProfileRow(
            id=cur.lastrowid, account_id=account_id, version=version,
            system_prompt=system_prompt,
            modifiers=modifiers or {}, hard_constraints=hard_constraints or {},
            soft_constraints=soft_constraints or {}, is_active=True, created_at=now,
        )

    def get_active_prompt_profile(self, account_id: str) -> PromptProfileRow | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM prompt_profiles WHERE account_id = ? AND is_active = 1", (account_id,)
            ).fetchone()
        if row is None:
            return None
        return self._prompt_row(dict(row))

    def list_prompt_versions(self, account_id: str) -> list[PromptProfileRow]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM prompt_profiles WHERE account_id = ? ORDER BY id DESC", (account_id,)
            ).fetchall()
        return [self._prompt_row(dict(r)) for r in rows]

    def rollback_prompt_profile(self, account_id: str, version: str) -> PromptProfileRow | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM prompt_profiles WHERE account_id = ? AND version = ?",
                (account_id, version),
            ).fetchone()
            if row is None:
                return None
            c.execute("UPDATE prompt_profiles SET is_active = 0 WHERE account_id = ?", (account_id,))
            c.execute(
                "UPDATE prompt_profiles SET is_active = 1 WHERE account_id = ? AND version = ?",
                (account_id, version),
            )
        return self._prompt_row(dict(row))

    @staticmethod
    def _prompt_row(d: dict) -> PromptProfileRow:
        return PromptProfileRow(
            id=d["id"], account_id=d["account_id"], version=d["version"],
            system_prompt=d["system_prompt"],
            modifiers=json.loads(d["modifiers_json"]),
            hard_constraints=json.loads(d["hard_constraints_json"]),
            soft_constraints=json.loads(d["soft_constraints_json"]),
            is_active=bool(d["is_active"]), created_at=d["created_at"],
        )

    # ------------------------------------------------------------------ #
    # Full Profile (merged dict для инжекции в A5 GenContext.profile)
    # ------------------------------------------------------------------ #

    def get_full_profile(self, account_id: str) -> dict | None:
        account = self.get_account(account_id)
        if account is None:
            return None
        result: dict = {
            "account_id": account.id,
            "name": account.name,
            "niche": account.niche_slug,
            "niche_slugs": account.niche_slugs,
            "language": account.language,
        }

        bb = self.get_brand_book(account_id)
        if bb:
            result["brand_book"] = {
                "tone_preset": bb.tone_preset,
                "tone_of_voice": {
                    "formality": bb.formality,
                    "energy": bb.energy,
                    "humor": bb.humor,
                    "expertise": bb.expertise,
                },
                "forbidden_words": bb.forbidden_words,
                "cta": bb.cta,
                **bb.extra,
            }

        aud = self.get_audience(account_id)
        if aud:
            result["audience"] = {
                "age_range": aud.age_range,
                "geography": aud.geography,
                "gender": aud.gender,
                "expertise_level": aud.expertise_level,
                "pain_points": aud.pain_points,
                "desires": aud.desires,
                **aud.extra,
            }

        pp = self.get_active_prompt_profile(account_id)
        if pp:
            result["system_prompt"] = pp.system_prompt
            result["modifiers"] = pp.modifiers
            result["hard_constraints"] = pp.hard_constraints
            result["soft_constraints"] = pp.soft_constraints
            result["prompt_version"] = pp.version

        return result
