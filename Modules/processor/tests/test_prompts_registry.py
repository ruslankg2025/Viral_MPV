"""Тесты Prompt Registry v2: store, миграция, CRUD через admin API."""
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
    (media / "downloads").mkdir(parents=True)
    db.mkdir()

    os.environ["MEDIA_DIR"] = str(media)
    os.environ["DB_DIR"] = str(db)
    os.environ["PROCESSOR_TOKEN"] = "test-worker"
    os.environ["PROCESSOR_ADMIN_TOKEN"] = "test-admin"
    os.environ["PROCESSOR_KEY_ENCRYPTION_KEY"] = FERNET
    for k in [
        "BOOTSTRAP_ASSEMBLYAI_API_KEY",
        "BOOTSTRAP_DEEPGRAM_API_KEY",
        "BOOTSTRAP_OPENAI_WHISPER_API_KEY",
        "BOOTSTRAP_GROQ_API_KEY",
        "BOOTSTRAP_ANTHROPIC_API_KEY",
        "BOOTSTRAP_OPENAI_API_KEY",
        "BOOTSTRAP_GOOGLE_GEMINI_API_KEY",
    ]:
        os.environ.pop(k, None)

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    for mod in list(sys.modules):
        if mod.startswith(("main", "config", "auth", "state", "jobs", "cache", "keys", "tasks", "clients", "prompts", "schemas")):
            sys.modules.pop(mod, None)

    from main import app  # noqa: E402

    with TestClient(app) as c:
        yield c


ADMIN = {"X-Admin-Token": "test-admin"}


def test_bootstrap_creates_three_builtin_prompts_as_v1(client):
    """После старта в БД должны быть три встроенных промпта как v1, активные."""
    r = client.get("/admin/prompts", headers=ADMIN)
    assert r.status_code == 200
    rows = r.json()
    names = {row["name"]: row for row in rows}
    assert "vision_default" in names
    assert "vision_detailed" in names
    assert "vision_hooks_focused" in names
    for row in rows:
        assert row["version"] == "v1"
        assert row["is_active"] is True


def test_create_new_version_and_activate(client):
    """Создаём v2 → активируем → старая v1 деактивируется."""
    new_body = "TEST PROMPT V2 — отвечай только цифрой."
    r = client.post(
        "/admin/prompts",
        json={
            "name": "vision_default",
            "version": "v2",
            "body": new_body,
            "is_active": False,
        },
        headers=ADMIN,
    )
    assert r.status_code == 201, r.text
    assert r.json()["version"] == "v2"
    assert r.json()["is_active"] is False

    # Активация v2 → v1 должна стать неактивной
    r = client.patch("/admin/prompts/vision_default/activate/v2", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["is_active"] is True

    versions = client.get("/admin/prompts/vision_default", headers=ADMIN).json()
    by_version = {v["version"]: v for v in versions}
    assert by_version["v1"]["is_active"] is False
    assert by_version["v2"]["is_active"] is True
    assert by_version["v2"]["body"] == new_body


def test_cannot_delete_active_version(client):
    """Активную версию удалять нельзя — 409."""
    r = client.delete("/admin/prompts/vision_default/v1", headers=ADMIN)
    assert r.status_code == 409


def test_can_delete_inactive_version(client):
    """Создаём v2 без активации, удаляем v2 → ок."""
    client.post(
        "/admin/prompts",
        json={"name": "vision_default", "version": "v2", "body": "tmp"},
        headers=ADMIN,
    )
    r = client.delete("/admin/prompts/vision_default/v2", headers=ADMIN)
    assert r.status_code == 204
    versions = client.get("/admin/prompts/vision_default", headers=ADMIN).json()
    assert all(v["version"] != "v2" for v in versions)


def test_get_prompt_record_uses_active_version_when_unset(client):
    """get_prompt_record без version возвращает активную (после активации новой v2 — её)."""
    # Активируем новую v2
    client.post(
        "/admin/prompts",
        json={"name": "vision_default", "version": "v2", "body": "BODY V2"},
        headers=ADMIN,
    )
    client.patch("/admin/prompts/vision_default/activate/v2", headers=ADMIN)

    from prompts import get_prompt_record

    rec = get_prompt_record("default")
    assert rec.version == "v2"
    assert rec.body == "BODY V2"
    assert rec.full_version == "vision_default:v2"


def test_get_prompt_record_with_explicit_version(client):
    """Можно явно запросить старую версию."""
    client.post(
        "/admin/prompts",
        json={"name": "vision_default", "version": "v2", "body": "BODY V2"},
        headers=ADMIN,
    )
    client.patch("/admin/prompts/vision_default/activate/v2", headers=ADMIN)

    from prompts import get_prompt_record

    rec = get_prompt_record("default", version="v1")
    assert rec.version == "v1"
    assert "JSON" in rec.body  # встроенная v1 содержит инструкцию про JSON


def test_create_duplicate_version_conflict(client):
    """Повторное создание (name, version) → 409."""
    r = client.post(
        "/admin/prompts",
        json={"name": "vision_default", "version": "v1", "body": "dup"},
        headers=ADMIN,
    )
    assert r.status_code == 409


def test_404_unknown_prompt_name(client):
    r = client.get("/admin/prompts/nonexistent", headers=ADMIN)
    assert r.status_code == 404


def test_404_unknown_version(client):
    r = client.get("/admin/prompts/vision_default/v999", headers=ADMIN)
    assert r.status_code == 404
