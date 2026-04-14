from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.profile", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    profile_token: str = Field(default="dev-token-change-me")
    profile_admin_token: str = Field(default="dev-admin-token-change-me")

    db_dir: Path = Field(default=Path("/db"))

    # Если True — при старте автоматически загружает example_account.json
    bootstrap_example: bool = Field(default=False)

    def ensure_dirs(self) -> None:
        self.db_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
