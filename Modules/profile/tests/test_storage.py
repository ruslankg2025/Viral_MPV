import sqlite3

import pytest


def test_create_account(store):
    acc = store.create_account("Test Account", niche_slug="tech/ai")
    assert acc.name == "Test Account"
    assert acc.niche_slug == "tech/ai"
    assert len(acc.id) == 36  # uuid4


def test_get_account_none(store):
    assert store.get_account("nonexistent") is None


def test_list_accounts(store):
    store.create_account("A")
    store.create_account("B")
    accounts = store.list_accounts()
    assert len(accounts) == 2


def test_update_account(store):
    acc = store.create_account("Old Name")
    ok = store.update_account(acc.id, name="New Name")
    assert ok
    updated = store.get_account(acc.id)
    assert updated.name == "New Name"


def test_update_account_not_found(store):
    assert store.update_account("ghost", name="X") is False


# ---- Brand Book ----

def test_upsert_brand_book_insert(store):
    acc = store.create_account("BB Test")
    bb = store.upsert_brand_book(acc.id, formality=5, energy=7, cta=["Subscribe"])
    assert bb.formality == 5
    assert bb.energy == 7
    assert bb.cta == ["Subscribe"]


def test_upsert_brand_book_merge_keeps_existing(store):
    acc = store.create_account("Merge Test")
    store.upsert_brand_book(acc.id, formality=5, forbidden_words=["bad"])
    # Второй upsert: передаём только energy, остальное None → должно остаться
    bb2 = store.upsert_brand_book(acc.id, energy=8)
    assert bb2.formality == 5          # сохранился
    assert bb2.energy == 8             # обновился
    assert bb2.forbidden_words == ["bad"]  # сохранился


def test_upsert_brand_book_only_one_row(store):
    acc = store.create_account("One BB")
    store.upsert_brand_book(acc.id, formality=3)
    store.upsert_brand_book(acc.id, formality=6)
    with store._conn() as c:
        count = c.execute("SELECT COUNT(*) FROM brand_books WHERE account_id=?", (acc.id,)).fetchone()[0]
    assert count == 1


# ---- Audience ----

def test_upsert_audience_insert_and_update(store):
    acc = store.create_account("Aud Test")
    store.upsert_audience(acc.id, age_range="25-35", pain_points=["pain1"])
    aud2 = store.upsert_audience(acc.id, geography="Russia")
    assert aud2.age_range == "25-35"    # сохранился
    assert aud2.geography == "Russia"   # обновился
    assert aud2.pain_points == ["pain1"]  # сохранился


def test_upsert_audience_only_one_row(store):
    acc = store.create_account("One Aud")
    store.upsert_audience(acc.id, age_range="20-30")
    store.upsert_audience(acc.id, age_range="30-40")
    with store._conn() as c:
        count = c.execute("SELECT COUNT(*) FROM audience_profiles WHERE account_id=?", (acc.id,)).fetchone()[0]
    assert count == 1


# ---- Prompt Profile ----

def test_prompt_profile_versioning(store):
    acc = store.create_account("PP Test")
    store.create_prompt_profile(acc.id, version="1.0", system_prompt="v1 prompt")
    store.create_prompt_profile(acc.id, version="1.1", system_prompt="v1.1 prompt")

    active = store.get_active_prompt_profile(acc.id)
    assert active.version == "1.1"
    assert active.is_active is True

    versions = store.list_prompt_versions(acc.id)
    assert len(versions) == 2
    inactive = next(v for v in versions if v.version == "1.0")
    assert inactive.is_active is False


def test_prompt_profile_unique_version(store):
    acc = store.create_account("Unique PP")
    store.create_prompt_profile(acc.id, version="1.0", system_prompt="v1")
    with pytest.raises(sqlite3.IntegrityError):
        # Деактивирует v1.0, потом INSERT с той же версией — нарушение UNIQUE
        store.create_prompt_profile(acc.id, version="1.0", system_prompt="v1 again")


def test_rollback_prompt_profile(store):
    acc = store.create_account("Rollback PP")
    store.create_prompt_profile(acc.id, version="1.0", system_prompt="v1")
    store.create_prompt_profile(acc.id, version="1.1", system_prompt="v1.1")

    result = store.rollback_prompt_profile(acc.id, "1.0")
    assert result is not None
    assert result.version == "1.0"

    active = store.get_active_prompt_profile(acc.id)
    assert active.version == "1.0"


def test_rollback_unknown_version(store):
    acc = store.create_account("Rollback Unknown")
    result = store.rollback_prompt_profile(acc.id, "99.0")
    assert result is None


# ---- Full Profile ----

def test_get_full_profile_complete(store):
    acc = store.create_account("Full Test", niche_slug="finance")
    store.upsert_brand_book(acc.id, formality=4, cta=["Subscribe"])
    store.upsert_audience(acc.id, age_range="30-45", pain_points=["no money"])
    store.create_prompt_profile(acc.id, version="1.0", system_prompt="You are...")

    profile = store.get_full_profile(acc.id)
    assert profile is not None
    assert profile["account_id"] == acc.id
    assert profile["niche"] == "finance"
    assert profile["brand_book"]["cta"] == ["Subscribe"]
    assert profile["audience"]["pain_points"] == ["no money"]
    assert profile["system_prompt"] == "You are..."
    assert profile["prompt_version"] == "1.0"


def test_get_full_profile_partial(store):
    acc = store.create_account("Partial Test")
    # Нет brand_book, audience, prompt
    profile = store.get_full_profile(acc.id)
    assert profile is not None
    assert profile.get("brand_book") is None
    assert profile.get("audience") is None
    assert profile.get("system_prompt") is None


def test_get_full_profile_not_found(store):
    assert store.get_full_profile("ghost") is None


# ---- Taxonomy ----

def test_seed_taxonomy_idempotent(store):
    entries = [
        {"slug": "tech", "label_ru": "Технологии"},
        {"slug": "tech/ai", "label_ru": "AI", "parent_slug": "tech"},
    ]
    added1 = store.seed_taxonomy(entries)
    added2 = store.seed_taxonomy(entries)
    assert added1 == 2
    assert added2 == 0  # уже существуют — INSERT OR IGNORE


def test_list_taxonomy_filter(store):
    store.seed_taxonomy([
        {"slug": "a", "label_ru": "A"},
        {"slug": "a/1", "label_ru": "A1", "parent_slug": "a"},
        {"slug": "b", "label_ru": "B"},
    ])
    children = store.list_taxonomy(parent_slug="a")
    assert len(children) == 1
    assert children[0]["slug"] == "a/1"


# ---- create_account_with_id ----

def test_create_account_with_id(store):
    acc = store.create_account_with_id("example", "Example Account", "business")
    assert acc.id == "example"
    assert store.get_account("example") is not None
