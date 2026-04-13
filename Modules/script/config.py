from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.script", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    script_token: str = Field(default="dev-worker-token-change-me")
    script_admin_token: str = Field(default="dev-admin-token-change-me")
    script_key_encryption_key: str = Field(default="")

    db_dir: Path = Field(default=Path("/db"))

    # Если 1 — вместо реального LLM используется FakeTextClient для локального dev.
    script_fake_llm: bool = Field(default=False)

    # Дефолтный провайдер text generation; выбирается resolver'ом из активных ключей.
    default_text_provider: str = Field(default="anthropic_claude_text")

    bootstrap_anthropic_api_key: str = ""
    bootstrap_openai_api_key: str = ""

    def ensure_dirs(self) -> None:
        self.db_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
