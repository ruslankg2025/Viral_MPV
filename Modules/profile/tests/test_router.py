import pytest

TOKEN = "test-token"
ADMIN_TOKEN = "test-admin-token"


def token_headers():
    return {"X-Token": TOKEN}


def admin_headers():
    return {"X-Admin-Token": ADMIN_TOKEN}


# ---- Health ----

def test_healthz_no_auth(client):
    r = client.get("/profile/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ---- Auth ----

def test_taxonomy_requires_token(client):
    r = client.get("/profile/taxonomy")
    assert r.status_code == 401


def test_taxonomy_with_token(client, store):
    store.seed_taxonomy([{"slug": "tech", "label_ru": "Технологии"}])
    r = client.get("/profile/taxonomy", headers=token_headers())
    assert r.status_code == 200
    assert any(e["slug"] == "tech" for e in r.json())


def test_taxonomy_filter_by_parent(client, store):
    store.seed_taxonomy([
        {"slug": "biz", "label_ru": "Бизнес"},
        {"slug": "biz/startup", "label_ru": "Стартапы", "parent_slug": "biz"},
    ])
    r = client.get("/profile/taxonomy?parent_slug=biz", headers=token_headers())
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["slug"] == "biz/startup"


# ---- Accounts CRUD ----

def test_create_and_get_account(client):
    r = client.post("/profile/accounts", json={"name": "Test Co", "niche_slug": "tech"}, headers=token_headers())
    assert r.status_code == 201
    account_id = r.json()["id"]

    r2 = client.get(f"/profile/accounts/{account_id}", headers=token_headers())
    assert r2.status_code == 200
    body = r2.json()
    assert body["account_id"] == account_id
    assert body["name"] == "Test Co"
    assert body["brand_book"] is None
    assert body["audience"] is None


def test_get_unknown_account(client):
    r = client.get("/profile/accounts/nonexistent", headers=token_headers())
    assert r.status_code == 404


def test_list_accounts(client):
    client.post("/profile/accounts", json={"name": "A"}, headers=token_headers())
    client.post("/profile/accounts", json={"name": "B"}, headers=token_headers())
    r = client.get("/profile/accounts", headers=token_headers())
    assert r.status_code == 200
    assert len(r.json()) >= 2


def test_patch_account(client):
    r = client.post("/profile/accounts", json={"name": "Old"}, headers=token_headers())
    aid = r.json()["id"]
    r2 = client.patch(f"/profile/accounts/{aid}", json={"name": "New"}, headers=token_headers())
    assert r2.status_code == 200
    assert r2.json()["name"] == "New"


# ---- Brand Book ----

def test_brand_book_validation_tone_out_of_range(client):
    r = client.post("/profile/accounts", json={"name": "Val Test"}, headers=token_headers())
    aid = r.json()["id"]
    r2 = client.put(
        f"/profile/accounts/{aid}/brand-book",
        json={"tone": {"formality": 11}},  # > 10
        headers=token_headers(),
    )
    assert r2.status_code == 422


def test_brand_book_upsert_and_merge(client):
    r = client.post("/profile/accounts", json={"name": "BB Merge"}, headers=token_headers())
    aid = r.json()["id"]

    client.put(
        f"/profile/accounts/{aid}/brand-book",
        json={"tone": {"formality": 5}, "forbidden_words": ["spam"]},
        headers=token_headers(),
    )
    # Второй PUT: обновляем только energy
    r2 = client.put(
        f"/profile/accounts/{aid}/brand-book",
        json={"tone": {"energy": 8}},
        headers=token_headers(),
    )
    assert r2.status_code == 200
    bb = r2.json()
    assert bb["tone_of_voice"]["formality"] == 5   # сохранился
    assert bb["tone_of_voice"]["energy"] == 8      # обновился
    assert bb["forbidden_words"] == ["spam"]        # сохранился


def test_get_brand_book_none(client):
    r = client.post("/profile/accounts", json={"name": "No BB"}, headers=token_headers())
    aid = r.json()["id"]
    r2 = client.get(f"/profile/accounts/{aid}/brand-book", headers=token_headers())
    assert r2.status_code == 200
    assert r2.json() is None


# ---- Audience ----

def test_audience_upsert(client):
    r = client.post("/profile/accounts", json={"name": "Aud"}, headers=token_headers())
    aid = r.json()["id"]
    r2 = client.put(
        f"/profile/accounts/{aid}/audience",
        json={"age_range": "25-35", "pain_points": ["p1", "p2"]},
        headers=token_headers(),
    )
    assert r2.status_code == 200
    assert r2.json()["age_range"] == "25-35"
    assert r2.json()["pain_points"] == ["p1", "p2"]


def test_audience_expertise_level_validation(client):
    r = client.post("/profile/accounts", json={"name": "ExLvl"}, headers=token_headers())
    aid = r.json()["id"]
    r2 = client.put(
        f"/profile/accounts/{aid}/audience",
        json={"expertise_level": "guru"},  # не входит в Literal
        headers=token_headers(),
    )
    assert r2.status_code == 422


# ---- Prompt Profile ----

def test_prompt_profile_versioning_via_api(client):
    r = client.post("/profile/accounts", json={"name": "PP API"}, headers=token_headers())
    aid = r.json()["id"]

    client.post(
        f"/profile/accounts/{aid}/prompt-profile",
        json={"version": "1.0", "system_prompt": "v1"},
        headers=token_headers(),
    )
    client.post(
        f"/profile/accounts/{aid}/prompt-profile",
        json={"version": "1.1", "system_prompt": "v1.1"},
        headers=token_headers(),
    )

    r_active = client.get(f"/profile/accounts/{aid}/prompt-profile", headers=token_headers())
    assert r_active.status_code == 200
    assert r_active.json()["version"] == "1.1"
    assert r_active.json()["is_active"] is True

    r_versions = client.get(f"/profile/accounts/{aid}/prompt-profile/versions", headers=token_headers())
    assert len(r_versions.json()) == 2


def test_rollback_via_api(client):
    r = client.post("/profile/accounts", json={"name": "Rollback"}, headers=token_headers())
    aid = r.json()["id"]

    client.post(f"/profile/accounts/{aid}/prompt-profile", json={"version": "1.0", "system_prompt": "v1"}, headers=token_headers())
    client.post(f"/profile/accounts/{aid}/prompt-profile", json={"version": "1.1", "system_prompt": "v1.1"}, headers=token_headers())

    r2 = client.post(f"/profile/accounts/{aid}/prompt-profile/rollback/1.0", headers=token_headers())
    assert r2.status_code == 200
    assert r2.json()["version"] == "1.0"

    r_active = client.get(f"/profile/accounts/{aid}/prompt-profile", headers=token_headers())
    assert r_active.json()["version"] == "1.0"


def test_rollback_unknown_version(client):
    r = client.post("/profile/accounts", json={"name": "RB Unknown"}, headers=token_headers())
    aid = r.json()["id"]
    r2 = client.post(f"/profile/accounts/{aid}/prompt-profile/rollback/99.0", headers=token_headers())
    assert r2.status_code == 404


# ---- Seed ----

def test_seed_requires_admin_token(client):
    r = client.post("/profile/seed", headers=token_headers())  # worker token — недостаточно
    assert r.status_code == 401


def test_seed_idempotent(client):
    r1 = client.post("/profile/seed", headers=admin_headers())
    assert r1.status_code == 200
    assert r1.json()["seeded"] is True

    r2 = client.post("/profile/seed", headers=admin_headers())
    assert r2.status_code == 200
    assert r2.json()["seeded"] is False  # уже существует


def test_full_profile_response_shape(client):
    client.post("/profile/seed", headers=admin_headers())
    r = client.get("/profile/accounts/example", headers=token_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["account_id"] == "example"
    assert body["brand_book"] is not None
    assert "tone_of_voice" in body["brand_book"]
    assert body["brand_book"]["tone_preset"] == "expert"
    assert body["niche_slugs"] == ["ai-neuro", "business", "investing"]
    assert body["language"] == "ru"
    assert body["audience"] is not None
    assert body["system_prompt"] is not None
    assert body["prompt_version"] == "1.0"


# ---- Multi-niche / language via API ----

def test_create_account_with_niche_slugs(client):
    r = client.post(
        "/profile/accounts",
        json={"name": "Multi", "niche_slugs": ["money", "investing", "business"], "language": "en"},
        headers=token_headers(),
    )
    assert r.status_code == 201
    body = r.json()
    assert body["niche_slugs"] == ["money", "investing", "business"]
    assert body["niche_slug"] == "money"
    assert body["language"] == "en"


def test_patch_account_niche_slugs(client):
    r = client.post("/profile/accounts", json={"name": "P"}, headers=token_headers())
    aid = r.json()["id"]
    r2 = client.patch(
        f"/profile/accounts/{aid}",
        json={"niche_slugs": ["business", "investing"]},
        headers=token_headers(),
    )
    assert r2.status_code == 200
    assert r2.json()["niche_slugs"] == ["business", "investing"]
    assert r2.json()["niche_slug"] == "business"


def test_account_niche_slugs_max_6(client):
    r = client.post(
        "/profile/accounts",
        json={"name": "X", "niche_slugs": ["a", "b", "c", "d", "e", "f", "g"]},
        headers=token_headers(),
    )
    assert r.status_code == 422  # нарушение max_length


# ---- DELETE account ----

def test_delete_account(client):
    r = client.post("/profile/accounts", json={"name": "bye"}, headers=token_headers())
    aid = r.json()["id"]

    r2 = client.delete(f"/profile/accounts/{aid}", headers=token_headers())
    assert r2.status_code == 204

    r3 = client.get(f"/profile/accounts/{aid}", headers=token_headers())
    assert r3.status_code == 404


def test_delete_account_not_found(client):
    r = client.delete("/profile/accounts/ghost", headers=token_headers())
    assert r.status_code == 404


def test_delete_requires_token(client):
    r = client.delete("/profile/accounts/anything")
    assert r.status_code == 401


# ---- Tone preset ----

def test_brand_book_tone_preset_fills_axes(client):
    r = client.post("/profile/accounts", json={"name": "Preset"}, headers=token_headers())
    aid = r.json()["id"]

    # Клиент шлёт только preset; backend подставляет дефолты осей
    r2 = client.put(
        f"/profile/accounts/{aid}/brand-book",
        json={"tone_preset": "expert"},
        headers=token_headers(),
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["tone_preset"] == "expert"
    assert body["tone_of_voice"]["formality"] == 8
    assert body["tone_of_voice"]["expertise"] == 9


def test_brand_book_tone_preset_invalid(client):
    r = client.post("/profile/accounts", json={"name": "InvPreset"}, headers=token_headers())
    aid = r.json()["id"]
    r2 = client.put(
        f"/profile/accounts/{aid}/brand-book",
        json={"tone_preset": "angry"},  # не из Literal
        headers=token_headers(),
    )
    assert r2.status_code == 422


# ---- Taxonomy type field ----

def test_taxonomy_returns_type(client, store):
    store.seed_taxonomy([
        {"slug": "money", "label_ru": "Деньги", "label_en": "Money", "type": "both"},
    ])
    r = client.get("/profile/taxonomy", headers=token_headers())
    assert r.status_code == 200
    by_slug = {e["slug"]: e for e in r.json()}
    assert by_slug["money"]["type"] == "both"
