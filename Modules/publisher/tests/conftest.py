import os
import sys
from pathlib import Path

import pytest

# Сервис-модули лежат в Modules/publisher (на уровень выше tests/).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    """Изолируем БД и сбрасываем кэш настроек на каждый тест."""
    monkeypatch.setenv("DB_DIR", str(tmp_path / "db"))
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path / "media"))
    # По умолчанию — dry-run (как в проде). Тесты live явно переопределяют.
    monkeypatch.setenv("DEFAULT_DRY_RUN", "true")
    monkeypatch.setenv("PUBLISHER_TOKEN", "test-worker-token")
    monkeypatch.setenv("PUBLISHER_ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setenv("VK_ACCESS_TOKEN", "test-vk-token")

    import config

    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()
