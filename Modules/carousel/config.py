from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.carousel", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    carousel_token: str = Field(default="dev-worker-token-change-me")
    carousel_admin_token: str = Field(default="dev-admin-token-change-me")
    carousel_key_encryption_key: str = Field(default="")

    db_dir: Path = Field(default=Path("/db"))
    media_dir: Path = Field(default=Path("/media"))

    # Если 1 — LLM-адаптация текста отключена, используется детерминированный
    # fallback-сплиттер (для локального dev без ключей).
    carousel_fake_llm: bool = Field(default=False)

    # Дефолтный провайдер text generation (resolver выберет из активных ключей).
    default_text_provider: str = Field(default="anthropic_claude_text")

    bootstrap_anthropic_api_key: str = ""
    bootstrap_openai_api_key: str = ""

    @property
    def carousel_media_dir(self) -> Path:
        return self.media_dir / "carousel"

    def ensure_dirs(self) -> None:
        self.db_dir.mkdir(parents=True, exist_ok=True)
        (self.carousel_media_dir / "templates").mkdir(parents=True, exist_ok=True)
        (self.carousel_media_dir / "out").mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
