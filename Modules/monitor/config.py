from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.monitor", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    monitor_token: str = Field(default="dev-token-change-me")
    monitor_admin_token: str = Field(default="dev-admin-token-change-me")

    db_dir: Path = Field(default=Path("/db"))

    youtube_api_key: str = Field(default="")

    # Apify (Instagram + TikTok)
    apify_token: str = Field(default="")
    apify_instagram_actor: str = Field(default="apify~instagram-reel-scraper")
    apify_instagram_profile_actor: str = Field(default="apify~instagram-profile-scraper")
    apify_tiktok_actor: str = Field(default="clockworks~tiktok-scraper")
    apify_timeout_sec: int = Field(default=180)
    apify_results_limit: int = Field(default=30)

    profile_base_url: str = Field(default="http://profile:8000")
    profile_token: str = Field(default="dev-token-change-me")

    monitor_fake_fetch: bool = Field(default=False)

    crawl_default_interval_min: int = Field(default=60)
    crawl_max_concurrent: int = Field(default=3)

    trending_zscore_threshold: float = Field(default=2.0)
    trending_growth_threshold: float = Field(default=0.5)
    trending_min_views: int = Field(default=100)

    # Watchlist — ежедневный auto-отбор top-N «на мониторинг».
    watchlist_enabled: bool = Field(default=True)
    watchlist_top_n: int = Field(default=5)
    watchlist_ttl_days: int = Field(default=3)
    watchlist_freshness_hours: int = Field(default=48)
    watchlist_min_age_hours: float = Field(default=2.0)
    watchlist_daily_run_utc: str = Field(default="08:00")  # HH:MM
    watchlist_graduate_velocity: float = Field(default=5000.0)
    watchlist_graduate_delta_pct: float = Field(default=2.0)

    def ensure_dirs(self) -> None:
        self.db_dir.mkdir(parents=True, exist_ok=True)

    @property
    def effective_fake_mode(self) -> bool:
        """Глобальный fake флаг (для healthz). True если forced или у YouTube нет ключа."""
        return self.monitor_fake_fetch or not self.youtube_api_key

    def fake_mode_for(self, platform: str) -> bool:
        """Per-platform fake mode: forced → always fake, иначе по наличию ключа."""
        if self.monitor_fake_fetch:
            return True
        if platform == "youtube":
            return not self.youtube_api_key
        if platform in ("instagram", "tiktok"):
            return not self.apify_token
        return True


@lru_cache
def get_settings() -> Settings:
    return Settings()
