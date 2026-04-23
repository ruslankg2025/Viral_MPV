"""
Scheduler wrapper поверх APScheduler AsyncIOScheduler.

Один job per source_id. При add_source / update_source / delete_source —
обновляем соответствующий job. При startup вызывается reload_from_db.

Особенности:
- max_instances=1 — не допускает параллельного запуска того же job.
- coalesce=True — пропускает накопившиеся misfires.
- misfire_grace_time=300 — допускаем 5 минут на задержку тика.
- anchor-based интервалы: start_date=anchor (по умолчанию 00:00 UTC) →
  IntervalTrigger(minutes=N) будет тикать в 00:00, 00:00+N, ... независимо
  от времени добавления источника. Для N=360 это 00/06/12/18 UTC.
"""
from datetime import datetime, timezone
from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from logging_setup import get_logger

log = get_logger("monitor.scheduler")


def _anchor_dt(anchor_hhmm: str) -> datetime:
    """Парсит 'HH:MM' → стабильную датировку в прошлом для start_date.
    APScheduler IntervalTrigger с start_date=T0 тикает в T0, T0+N, T0+2N...
    Используем фиксированный anchor-день далеко в прошлом, чтобы выравнивание
    не зависело от момента добавления job'а."""
    try:
        hh, mm = anchor_hhmm.split(":")
        h, m = int(hh), int(mm)
    except Exception:
        h, m = 0, 0
    return datetime(2000, 1, 1, h, m, 0, tzinfo=timezone.utc)


class SchedulerWrapper:
    def __init__(self, crawl_callback: Callable, crawl_anchor_utc: str = "00:00"):
        """crawl_callback: async def (source_id: str) -> None"""
        self._scheduler = AsyncIOScheduler(
            job_defaults={
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 300,
            }
        )
        self._crawl_callback = crawl_callback
        self._crawl_anchor_utc = crawl_anchor_utc
        self._started = False

    def set_anchor(self, anchor_hhmm: str) -> None:
        """Изменить anchor. Применится к ближайшему reload_from_sources."""
        self._crawl_anchor_utc = anchor_hhmm

    def start(self) -> None:
        if not self._started:
            self._scheduler.start()
            self._started = True
            log.info("scheduler_started")

    def stop(self) -> None:
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False
            log.info("scheduler_stopped")

    @property
    def running(self) -> bool:
        return self._started

    def add_source_job(self, source_id: str, interval_min: int) -> None:
        job_id = self._job_id(source_id)
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)
        anchor = _anchor_dt(self._crawl_anchor_utc)
        self._scheduler.add_job(
            self._crawl_callback,
            trigger=IntervalTrigger(minutes=interval_min, start_date=anchor),
            args=[source_id],
            id=job_id,
            replace_existing=True,
        )
        log.info(
            "scheduler_job_added",
            source_id=source_id,
            interval_min=interval_min,
            anchor_utc=self._crawl_anchor_utc,
        )

    def remove_source_job(self, source_id: str) -> None:
        job_id = self._job_id(source_id)
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)
            log.info("scheduler_job_removed", source_id=source_id)

    def reload_from_sources(self, sources: list) -> int:
        """Полная перезагрузка jobs из списка sources (только активные).
        Возвращает количество добавленных jobs.
        """
        # Удаляем все существующие
        for job in list(self._scheduler.get_jobs()):
            if job.id.startswith("src:"):
                self._scheduler.remove_job(job.id)

        count = 0
        for s in sources:
            if s.is_active:
                self.add_source_job(s.id, s.interval_min)
                count += 1
        log.info("scheduler_reloaded", count=count)
        return count

    def list_jobs(self) -> list[dict]:
        result = []
        for job in self._scheduler.get_jobs():
            result.append(
                {
                    "id": job.id,
                    "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                    "trigger": str(job.trigger),
                }
            )
        return result

    @staticmethod
    def _job_id(source_id: str) -> str:
        return f"src:{source_id}"
