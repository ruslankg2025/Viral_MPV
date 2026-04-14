import os
import sys

import pytest

# Добавляем корень модуля в sys.path для импортов без пакета
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

# Подавляем чтение .env.profile при тестах
os.environ["PROFILE_TOKEN"] = "test-token"
os.environ["PROFILE_ADMIN_TOKEN"] = "test-admin-token"
os.environ["BOOTSTRAP_EXAMPLE"] = "0"


@pytest.fixture
def store(tmp_path):
    from storage import ProfileStore

    return ProfileStore(tmp_path / "test_profile.db")


@pytest.fixture
def client(store):
    from fastapi.testclient import TestClient

    # Патчим state до импорта app
    import state as _state

    _state.state.profile_store = store
    _state.state.settings = __import__("config").get_settings()

    from main import app

    return TestClient(app)


TOKEN = "test-token"
ADMIN_TOKEN = "test-admin-token"


def token_headers():
    return {"X-Token": TOKEN}


def admin_headers():
    return {"X-Admin-Token": ADMIN_TOKEN}
