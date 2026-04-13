"""Интеграционные тесты /script/* через TestClient + FakeTextClient."""
import json
import os
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from viral_llm.clients.base import GenerationResult  # noqa: E402

FERNET = Fernet.generate_key().decode()


VALID_BODY = {
    "meta": {
        "template": "reels_hook_v1",
        "template_version": "v1",
        "language": "ru",
        "target_duration_sec": 30,
        "format": "reels",
    },
    "hook": {"text": "Интригующий hook", "estimated_duration_sec": 3.0},
    "body": [
        {"scene": 1, "text": "Сцена 1", "estimated_duration_sec": 10.0, "visual_hint": ""},
        {"scene": 2, "text": "Сцена 2", "estimated_duration_sec": 13.0, "visual_hint": ""},
    ],
    "cta": {"text": "Подписывайся", "estimated_duration_sec": 4.0},
    "hashtags": ["#a", "#b", "#c"],
    "_schema_version": "1.0",
}


class _FakeClient:
    provider = "anthropic_claude_text"
    default_model = "claude-sonnet-4-6"

    def __init__(self):
        self.next_response: str = json.dumps(VALID_BODY, ensure_ascii=False)
        self.calls = 0

    async def generate(self, *, system, user, api_key, max_tokens=2048, model=None):
        self.calls += 1
        return GenerationResult(
            text=self.next_response,
            provider=self.provider,
            model=self.default_model,
            input_tokens=100,
            output_tokens=50,
            latency_ms=20,
        )


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "db"
    db.mkdir()

    os.environ["DB_DIR"] = str(db)
    os.environ["SCRIPT_TOKEN"] = "test-worker"
    os.environ["SCRIPT_ADMIN_TOKEN"] = "test-admin"
    os.environ["SCRIPT_KEY_ENCRYPTION_KEY"] = FERNET
    os.environ.pop("BOOTSTRAP_ANTHROPIC_API_KEY", None)
    os.environ.pop("BOOTSTRAP_OPENAI_API_KEY", None)

    for mod in list(sys.modules):
        if mod.startswith((
            "main", "config", "state", "auth",
            "router", "admin_keys", "admin_templates",
            "storage", "templates", "builtin_templates",
            "generator", "constraints", "export", "schemas",
        )):
            sys.modules.pop(mod, None)

    from main import app  # noqa: E402
    import viral_llm.clients.registry as reg  # noqa: E402

    fake = _FakeClient()
    monkeypatch.setitem(reg.TEXT_GENERATION_CLIENTS, "anthropic_claude_text", fake)

    with TestClient(app) as c:
        yield c, fake


WORKER = {"X-Worker-Token": "test-worker"}
ADMIN = {"X-Admin-Token": "test-admin"}


def _seed_key(c):
    r = c.post(
        "/script/admin/api-keys",
        json={"provider": "anthropic_claude_text", "secret": "sk-fake", "label": "main"},
        headers=ADMIN,
    )
    assert r.status_code == 201, r.text


def _gen_payload(topic="t") -> dict:
    return {
        "template": "reels_hook_v1",
        "profile": {"niche": "tech"},
        "params": {"topic": topic, "duration_sec": 30, "language": "ru", "format": "reels"},
    }


def test_healthz(client):
    c, _ = client
    r = c.get("/script/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "default_provider" in body


def test_generate_happy_path(client):
    c, fake = client
    _seed_key(c)

    r = c.post("/script/generate", json=_gen_payload(), headers=WORKER)
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["status"] == "ok"
    assert rec["body"]["hook"]["text"] == "Интригующий hook"
    assert rec["cost_usd"] > 0
    assert rec["parent_id"] is None
    assert rec["root_id"] == rec["id"]
    assert fake.calls == 1


def test_generate_without_key_fails(client):
    c, _ = client
    r = c.post("/script/generate", json=_gen_payload(), headers=WORKER)
    assert r.status_code == 503


def test_generate_without_auth_fails(client):
    c, _ = client
    r = c.post("/script/generate", json=_gen_payload())
    assert r.status_code == 401


def test_get_version(client):
    c, _ = client
    _seed_key(c)
    created = c.post("/script/generate", json=_gen_payload(), headers=WORKER).json()
    r = c.get(f"/script/{created['id']}", headers=WORKER)
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


def test_fork_creates_child(client):
    c, fake = client
    _seed_key(c)
    base = c.post("/script/generate", json=_gen_payload(), headers=WORKER).json()

    r = c.post(
        f"/script/{base['id']}/fork",
        json={"override": {"params": {"tone": "серьёзный"}}},
        headers=WORKER,
    )
    assert r.status_code == 201, r.text
    child = r.json()
    assert child["parent_id"] == base["id"]
    assert child["root_id"] == base["id"]
    assert fake.calls == 2


def test_tree_returns_all(client):
    c, _ = client
    _seed_key(c)
    base = c.post("/script/generate", json=_gen_payload(), headers=WORKER).json()
    c.post(f"/script/{base['id']}/fork", json={"override": {}}, headers=WORKER)

    r = c.get(f"/script/tree/{base['id']}", headers=WORKER)
    assert r.status_code == 200
    tree = r.json()
    assert len(tree) == 2


def test_delete_with_children_409(client):
    c, _ = client
    _seed_key(c)
    base = c.post("/script/generate", json=_gen_payload(), headers=WORKER).json()
    c.post(f"/script/{base['id']}/fork", json={"override": {}}, headers=WORKER)

    r = c.delete(f"/script/{base['id']}", headers=WORKER)
    assert r.status_code == 409


def test_delete_leaf_204(client):
    c, _ = client
    _seed_key(c)
    base = c.post("/script/generate", json=_gen_payload(), headers=WORKER).json()

    r = c.delete(f"/script/{base['id']}", headers=WORKER)
    assert r.status_code == 204


def test_export_markdown(client):
    c, _ = client
    _seed_key(c)
    base = c.post("/script/generate", json=_gen_payload("Моя тема"), headers=WORKER).json()

    r = c.get(f"/script/{base['id']}/export/markdown", headers=WORKER)
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    assert "# Моя тема" in r.text
    assert "Интригующий hook" in r.text


def test_export_json(client):
    c, _ = client
    _seed_key(c)
    base = c.post("/script/generate", json=_gen_payload(), headers=WORKER).json()

    r = c.get(f"/script/{base['id']}/export/json", headers=WORKER)
    assert r.status_code == 200
    parsed = json.loads(r.text)
    assert parsed["_schema_version"] == "1.0"


def test_export_docx_501(client):
    c, _ = client
    _seed_key(c)
    base = c.post("/script/generate", json=_gen_payload(), headers=WORKER).json()

    r = c.get(f"/script/{base['id']}/export/docx", headers=WORKER)
    assert r.status_code == 501


def test_retry_on_failed_constraint_creates_two_records(client):
    c, fake = client
    _seed_key(c)

    bad = json.loads(json.dumps(VALID_BODY))
    bad["body"] = [{"scene": 1, "text": "s", "estimated_duration_sec": 2.0, "visual_hint": ""}]
    bad["hook"]["estimated_duration_sec"] = 1.0
    bad["cta"]["estimated_duration_sec"] = 1.0
    responses = [json.dumps(bad, ensure_ascii=False), json.dumps(VALID_BODY, ensure_ascii=False)]

    async def fake_generate(*, system, user, api_key, max_tokens=2048, model=None):
        fake.calls += 1
        text = responses.pop(0) if responses else responses[-1]
        return GenerationResult(
            text=text, provider=fake.provider, model=fake.default_model,
            input_tokens=100, output_tokens=50, latency_ms=20,
        )

    fake.generate = fake_generate  # type: ignore[method-assign]

    r = c.post("/script/generate", json=_gen_payload(), headers=WORKER)
    assert r.status_code == 201, r.text
    final = r.json()
    assert final["status"] == "ok"
    assert final["parent_id"] is not None

    tree = c.get(f"/script/tree/{final['root_id']}", headers=WORKER).json()
    assert len(tree) == 2
    first = [v for v in tree if v["parent_id"] is None][0]
    assert first["status"] == "validation_failed"


def test_admin_templates_list_contains_builtin(client):
    c, _ = client
    r = c.get("/script/admin/templates", headers=ADMIN)
    assert r.status_code == 200
    names = {t["name"] for t in r.json()}
    assert "reels_hook_v1" in names
    assert "shorts_story_v1" in names
    assert "long_explainer_v1" in names
