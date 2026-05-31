"""Конфиг analytics-сервиса — паттерн как в carousel/config.py.

Все настройки читаются из .env.analytics (или env-переменных).
"""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.analytics", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Auth-токен для входящих запросов (от shell). Тот же токен сервис
    # отправляет другим сервисам в заголовке X-Worker-Token.
    analytics_token: str = Field(default="dev-analytics-token-change-me")

    db_dir: Path = Field(default=Path("/db"))

    # Publisher — читаем публикации НАПРЯМУЮ (не через shell). Порт 8000.
    publisher_url: str = Field(default="http://publisher:8000")
    # Токен, который шлём publisher-у в X-Worker-Token.
    publisher_token: str = Field(default="")

    # Если 1 — fetcher всегда использует mock-фикстуры (для dev без publisher).
    analytics_use_mock: bool = Field(default=False)

    # Интервалы воркера (секунды). Можно ужать в тестах.
    fetch_interval_seconds: int = Field(default=3600)  # раз в час
    snapshot_check_seconds: int = Field(default=300)   # как часто проверять 00:00 МСК

    # Запускать фоновый fetcher в lifespan (в тестах выключаем).
    enable_fetcher: bool = Field(default=True)

    def ensure_dirs(self) -> None:
        self.db_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
