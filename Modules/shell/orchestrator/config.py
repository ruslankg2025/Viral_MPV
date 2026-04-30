from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class OrchestratorSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.shell", ".env.downloader", ".env.processor", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_dir: Path = Field(default=Path("/db"))
    media_dir: Path = Field(default=Path("/media"))

    downloader_url: str = Field(default="http://downloader:8000")
    downloader_token: str = Field(default="dev-downloader-token-change-me")

    processor_url: str = Field(default="http://processor:8000")
    processor_token: str = Field(default="dev-worker-token-change-me")

    monitor_url: str = Field(default="http://monitor:8000")
    monitor_token: str = Field(default="dev-token-change-me")

    profile_url: str = Field(default="http://profile:8000")
    profile_token: str = Field(default="dev-token-change-me")

    # Пустая строка = generate шаг отключён (pipeline заканчивается на analyze)
    script_url: str = Field(default="")
    script_token: str = Field(default="dev-worker-token-change-me")

    # Полный таймаут одного run-а (от queued до done/failed). Защита от подвисших job-ов.
    orchestrator_run_timeout_sec: int = Field(default=600)
    # Поллинг GET /jobs/{id} у downloader/processor
    orchestrator_poll_interval_sec: float = Field(default=1.0)
    # Recovery loop: run, не обновлявшийся дольше этого, считаем зависшим после crash
    orchestrator_stalled_timeout_sec: int = Field(default=300)
    # Heartbeat: пульсация updated_at во время длинных wait_done. Должно быть < stalled_timeout/2
    # чтобы при честном падении shell-а run в любом случае попал в stalled-recovery.
    orchestrator_heartbeat_interval_sec: int = Field(default=30)
    # TTL для terminal runs (done/failed) — старше этого удаляются cleanup-loop-ом.
    # Default 30 дней. Активные runs не трогаются никогда.
    orchestrator_runs_ttl_days: int = Field(default=30)
    # Период между проходами cleanup-loop-а (1 час по умолчанию)
    orchestrator_cleanup_interval_sec: int = Field(default=3600)

    def ensure_dirs(self) -> None:
        self.db_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_orchestrator_settings() -> OrchestratorSettings:
    return OrchestratorSettings()
