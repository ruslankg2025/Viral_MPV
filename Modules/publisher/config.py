from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.publisher", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Auth-токены входящих запросов.
    publisher_token: str = Field(default="dev-worker-token-change-me")
    publisher_admin_token: str = Field(default="dev-admin-token-change-me")

    db_dir: Path = Field(default=Path("/db"))
    media_dir: Path = Field(default=Path("/media"))

    # ── VK ──────────────────────────────────────────────────────────────────
    # Токен доступа VK (user/community). Без него live-публикация невозможна.
    vk_access_token: str = Field(default="")
    # API-версия VK.
    vk_api_version: str = Field(default="5.199")
    # owner_id / group_id для wall.post и video.save (опционально, "-" перед id группы).
    vk_group_id: str = Field(default="")

    # ── dry-run ─────────────────────────────────────────────────────────────
    # КРИТИЧНО: по умолчанию TRUE — реальных HTTP к api.vk.com быть не должно,
    # пока явно не выключено в env.
    default_dry_run: bool = Field(default=True)

    @property
    def db_path(self) -> Path:
        return self.db_dir / "publications.db"

    def ensure_dirs(self) -> None:
        self.db_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
