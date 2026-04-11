from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.processor", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    processor_token: str = Field(default="dev-worker-token-change-me")
    processor_admin_token: str = Field(default="dev-admin-token-change-me")
    processor_key_encryption_key: str = Field(default="")

    media_dir: Path = Field(default=Path("/media"))
    db_dir: Path = Field(default=Path("/db"))

    max_concurrent_transcribe: int = Field(default=2)
    max_concurrent_vision: int = Field(default=4)

    test_ui_enabled: bool = Field(default=True)

    bootstrap_assemblyai_api_key: str = ""
    bootstrap_deepgram_api_key: str = ""
    bootstrap_openai_whisper_api_key: str = ""
    bootstrap_groq_api_key: str = ""
    bootstrap_anthropic_api_key: str = ""
    bootstrap_openai_api_key: str = ""
    bootstrap_google_gemini_api_key: str = ""

    def ensure_dirs(self) -> None:
        self.media_dir.mkdir(parents=True, exist_ok=True)
        (self.media_dir / "downloads").mkdir(parents=True, exist_ok=True)
        (self.media_dir / "audio").mkdir(parents=True, exist_ok=True)
        (self.media_dir / "frames").mkdir(parents=True, exist_ok=True)
        (self.media_dir / "transcripts").mkdir(parents=True, exist_ok=True)
        self.db_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
