import os
import sys
from pathlib import Path

import pytest

# Добавляем parent dir в sys.path, чтобы импортировать модули monitor
MONITOR_DIR = Path(__file__).resolve().parent.parent
if str(MONITOR_DIR) not in sys.path:
    sys.path.insert(0, str(MONITOR_DIR))

# Минимальные env для тестов до импортов
os.environ.setdefault("MONITOR_TOKEN", "test-user-token")
os.environ.setdefault("MONITOR_ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("MONITOR_FAKE_FETCH", "true")
os.environ.setdefault("PROFILE_BASE_URL", "http://mock-profile:8000")
os.environ.setdefault("PROFILE_TOKEN", "test-profile-token")


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_monitor.db"


@pytest.fixture
def store(tmp_db_path: Path):
    from storage import MonitorStore
    return MonitorStore(tmp_db_path)


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Сбрасываем lru_cache get_settings между тестами, чтобы env изменения подхватывались."""
    from config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
