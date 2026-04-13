import os
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


FERNET = Fernet.generate_key().decode()


@pytest.fixture()
def client(tmp_path):
    media = tmp_path / "media"
    db = tmp_path / "db"
    media.mkdir(); (media / "downloads").mkdir(); db.mkdir()

    os.environ["MEDIA_DIR"] = str(media)
    os.environ["DB_DIR"] = str(db)
    os.environ["PROCESSOR_TOKEN"] = "test-worker"
    os.environ["PROCESSOR_ADMIN_TOKEN"] = "test-admin"
    os.environ["PROCESSOR_KEY_ENCRYPTION_KEY"] = FERNET
    # Один bootstrap-ключ — проверим, что он вставится при старте
    os.environ["BOOTSTRAP_ASSEMBLYAI_API_KEY"] = "aa-bootstrap-secret-xyz"
    os.environ["BOOTSTRAP_ANTHROPIC_API_KEY"] = "sk-ant-bootstrap-secret-xyz"
    # Устанавливаем в "" (не pop) чтобы перезаписать значения из .env.processor,
    # иначе pydantic-settings подтянет реальные ключи из файла.
    for k in [
        "BOOTSTRAP_DEEPGRAM_API_KEY",
        "BOOTSTRAP_OPENAI_WHISPER_API_KEY",
        "BOOTSTRAP_GROQ_API_KEY",
        "BOOTSTRAP_OPENAI_API_KEY",
        "BOOTSTRAP_GOOGLE_GEMINI_API_KEY",
    ]:
        os.environ[k] = ""

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "auth", "state", "jobs", "cache", "tasks", "api", "prompts")):
            sys.modules.pop(mod, None)

    from main import app  # noqa: E402

    with TestClient(app) as c:
        yield c


ADMIN = {"X-Admin-Token": "test-admin"}


def test_crypto_roundtrip():
    from viral_llm.keys.crypto import KeyCrypto, mask_secret

    c = KeyCrypto(FERNET)
    enc = c.encrypt("hello-world-12345")
    assert enc != b"hello-world-12345"
    assert c.decrypt(enc) == "hello-world-12345"
    assert mask_secret("sk-1234567890abcdef") == "sk-123***cdef"


def test_bootstrap_creates_keys_on_first_start(client):
    r = client.get("/admin/api-keys", headers=ADMIN)
    assert r.status_code == 200
    keys = r.json()
    providers = {k["provider"] for k in keys}
    # assemblyai и anthropic_claude должны появиться из BOOTSTRAP_* env
    assert "assemblyai" in providers
    assert "anthropic_claude" in providers
    # все остальные не должны
    assert "deepgram" not in providers
    # секрет должен быть замаскирован
    for k in keys:
        assert "secret" not in k
        assert k["secret_masked"] != "aa-bootstrap-secret-xyz"
        assert "***" in k["secret_masked"] or "*" in k["secret_masked"]


def test_admin_providers_list(client):
    r = client.get("/admin/providers", headers=ADMIN)
    assert r.status_code == 200
    providers = r.json()
    assert "assemblyai" in providers
    assert "anthropic_claude" in providers
    assert providers["assemblyai"]["kind"] == "transcription"
    assert providers["anthropic_claude"]["kind"] == "vision"


def test_crud_lifecycle(client):
    # create
    r = client.post(
        "/admin/api-keys",
        json={
            "provider": "deepgram",
            "label": "test-dg",
            "secret": "dg-secret-123",
            "priority": 50,
            "monthly_limit_usd": 100.0,
        },
        headers=ADMIN,
    )
    assert r.status_code == 201, r.text
    key = r.json()
    key_id = key["id"]
    assert key["provider"] == "deepgram"
    assert key["kind"] == "transcription"
    assert key["priority"] == 50
    assert key["is_active"] is True

    # list contains
    r = client.get("/admin/api-keys", headers=ADMIN)
    assert any(k["id"] == key_id for k in r.json())

    # get by id
    r = client.get(f"/admin/api-keys/{key_id}", headers=ADMIN)
    assert r.status_code == 200
    assert "usage_30d" in r.json()

    # patch
    r = client.patch(
        f"/admin/api-keys/{key_id}",
        json={"is_active": False, "priority": 200},
        headers=ADMIN,
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is False
    assert r.json()["priority"] == 200

    # delete
    r = client.delete(f"/admin/api-keys/{key_id}", headers=ADMIN)
    assert r.status_code == 204
    r = client.get(f"/admin/api-keys/{key_id}", headers=ADMIN)
    assert r.status_code == 404


def test_unknown_provider_rejected(client):
    r = client.post(
        "/admin/api-keys",
        json={"provider": "nonsense", "secret": "x"},
        headers=ADMIN,
    )
    assert r.status_code == 400


def test_admin_requires_admin_token(client):
    r = client.get("/admin/api-keys")
    assert r.status_code == 401
    r = client.get("/admin/api-keys", headers={"X-Admin-Token": "wrong"})
    assert r.status_code == 401


def test_healthz_reports_active_keys(client):
    r = client.get("/healthz")
    body = r.json()
    # из bootstrap: 1 transcription (assemblyai) + 1 vision (anthropic)
    assert body["active_keys"]["transcription"] >= 1
    assert body["active_keys"]["vision"] >= 1


def test_bootstrap_is_idempotent(client):
    # на первом старте создано 2 ключа из env. Restart должен не дублировать.
    count_before = len(client.get("/admin/api-keys", headers=ADMIN).json())

    from main import llm_bootstrap_config
    from state import state
    from viral_llm.keys.bootstrap import bootstrap_from_config

    created = bootstrap_from_config(llm_bootstrap_config(state.settings), state.key_store)
    assert created == 0, "повторный bootstrap не должен создавать ключи"

    count_after = len(client.get("/admin/api-keys", headers=ADMIN).json())
    assert count_before == count_after
