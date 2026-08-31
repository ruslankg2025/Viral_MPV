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

    # ── Telegram (Phase 3) ────────────────────────────────────────────────────
    telegram_bot_token: str = Field(default="")
    telegram_channel_id: str = Field(default="")  # @username или -100… id

    # ── YouTube Shorts (Phase 3) ──────────────────────────────────────────────
    youtube_client_id: str = Field(default="")
    youtube_client_secret: str = Field(default="")
    youtube_refresh_token: str = Field(default="")

    # ── Pinterest (Phase 2) ───────────────────────────────────────────────────
    pinterest_app_id: str = Field(default="")
    pinterest_app_secret: str = Field(default="")
    pinterest_refresh_token: str = Field(default="")
    pinterest_board_id: str = Field(default="")

    # Residential proxy для РФ-ограниченных площадок (Pinterest). Пусто → без прокси.
    http_proxy: str = Field(default="")

    # ── anti-spam / scheduling (Phase 4) ──────────────────────────────────────
    # Разброс расписания: при планировании нескольких публикаций раздвигаем их
    # по времени, чтобы не публиковать одновременно на разных площадках.
    # offset(index) = index*step ± jitter, где jitter детерминирован по seed/id.
    schedule_spread_step_min: int = Field(default=15)    # шаг между площадками, мин
    schedule_spread_jitter_min: int = Field(default=15)  # амплитуда «дрожания», мин
    # Rate-cap: если за последние N часов на платформе было >= M публикаций —
    # отдаём предупреждение (НЕ блокируем).
    antispam_rate_window_hours: int = Field(default=4)
    antispam_rate_limit: int = Field(default=3)
    # Content-cooldown: если похожий контент публиковался за последние K часов —
    # предупреждение.
    antispam_content_cooldown_hours: int = Field(default=24)

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
