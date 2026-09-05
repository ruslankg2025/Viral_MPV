"""Настройки сервиса montage.

Автомонтаж — тяжёлый рендер на общем 8ГБ-дроплете (соседи multibrocker/ccpm
24/7). Реальные предохранители: `mem_limit` в compose + строго 1 джоб (один
воркер) + ночное окно + проверка свободной памяти перед стартом. См.
[[montage-build]], [[prod-droplet-shared-with-other-projects]].
"""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.montage", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Токен входящих запросов (инжектит shell-прокси, X-Worker-Token).
    montage_token: str = Field(default="dev-worker-token-change-me")

    db_dir: Path = Field(default=Path("/db"))
    # Том с сырьём и результатами джобов (montage_data).
    work_dir: Path = Field(default=Path("/montage"))
    # Корень установки OpenMontage внутри образа (движок; Rule Zero).
    openmontage_root: Path = Field(default=Path("/opt/OpenMontage"))

    # ── ночное окно ───────────────────────────────────────────────────────────
    # Джоб СТАВИТСЯ в очередь в любое время, но воркер СТАРТУЕТ его только в окне
    # [night_start_hour, night_end_hour) по локальному времени контейнера (TZ),
    # если не задан force. Память проверяется ВСЕГДА, даже при force.
    night_enabled: bool = Field(default=True)
    night_start_hour: int = Field(default=1)   # 01:00
    night_end_hour: int = Field(default=7)     # 07:00

    # ── память ────────────────────────────────────────────────────────────────
    # Минимум MemAvailable (МБ, из /proc/meminfo — память ВСЕЙ машины) для старта
    # джоба. Ниже — воркер ждёт (соседи могли занять RAM). Замер smoke tiny/1080:
    # пик ~1.3ГБ; для base/large + 4К брать запас.
    min_avail_mb: int = Field(default=2500)

    # Жёсткий таймаут на джоб (сек) — убиваем зависший/своп-затык (dead-letter).
    job_timeout_sec: int = Field(default=5400)  # 90 мин

    # Период опроса очереди воркером (сек).
    poll_interval_sec: int = Field(default=30)

    # Ограничение потоков CPU для рендера (OMP/ctranslate2) — бережём соседей на
    # общем дроплете. Прокидываем в субпроцесс оркестратора.
    cpu_threads: int = Field(default=2)

    # ── дефолты рендера ──────────────────────────────────────────────────────
    default_model: str = Field(default="base")   # whisper model_size
    default_width: int = Field(default=2160)
    default_height: int = Field(default=3840)
    default_language: str = Field(default="ru")

    # ── загрузка / диск ────────────────────────────────────────────────────────
    # cap на исходник. Держим ≤ nginx client_max_body_size (сейчас 320M на проде).
    # Для мультикама/большого сырья поднять И nginx, И это значение.
    max_upload_mb: int = Field(default=300)
    # Автоочистка: результаты джобов старше N часов удаляются (cap диска).
    result_ttl_hours: int = Field(default=72)

    @property
    def db_path(self) -> Path:
        return self.db_dir / "montage.db"

    @property
    def jobs_dir(self) -> Path:
        return self.work_dir / "jobs"

    @property
    def incoming_dir(self) -> Path:
        # Сюда shell стримит аплоад перед созданием джоба (/jobs/from-path).
        return self.work_dir / "incoming"

    def ensure_dirs(self) -> None:
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.incoming_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
