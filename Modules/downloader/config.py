from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.downloader", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    downloader_token: str = Field(default="dev-downloader-token-change-me")

    media_dir: Path = Field(default=Path("/media"))
    db_dir: Path = Field(default=Path("/db"))
    fixture_path: Path = Field(default=Path("/app/fixtures/test_fixture.mp4"))

    # Этап 1: stub-режим возвращает фикстуру вместо реального скачивания
    stub_mode: bool = Field(default=True)

    # Этап 4+: живые стратегии
    apify_token: str = Field(default="")
    apify_instagram_actor: str = Field(default="apify~instagram-scraper")
    apify_tiktok_actor: str = Field(default="clockworks~tiktok-scraper")
    instagram_cookies_file: str = Field(default="")

    max_concurrent: int = Field(default=3)
    job_timeout_sec: int = Field(default=300)
    ttl_failed_hours: int = Field(default=24)

    def ensure_dirs(self) -> None:
        self.media_dir.mkdir(parents=True, exist_ok=True)
        (self.media_dir / "downloads").mkdir(parents=True, exist_ok=True)
        self.db_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
