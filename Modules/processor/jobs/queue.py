import asyncio
from typing import Any, Awaitable, Callable

from jobs.store import JobKind, JobStore
from logging_setup import get_logger

log = get_logger("jobs.queue")

JobHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class JobQueue:
    """
    Простая per-kind очередь с ограничением одновременных задач.

    Для transcribe и vision разные лимиты конкуретности. Каждый handler
    получает (job_id, payload) и должен вернуть dict с результатом.
    """

    def __init__(
        self,
        store: JobStore,
        limits: dict[str, int],
        handlers: dict[str, JobHandler],
    ):
        self.store = store
        self.limits = limits
        self.handlers = handlers
        self._queues: dict[str, asyncio.Queue[str]] = {
            kind: asyncio.Queue() for kind in limits
        }
        self._workers: list[asyncio.Task] = []
        self._active: dict[str, int] = {kind: 0 for kind in limits}
        self._stopped = False

    def _group(self, kind: JobKind) -> str:
        if kind == "transcribe":
            return "transcribe"
        if kind == "vision_analyze":
            return "vision"
        if kind == "extract_frames":
            return "frames"
        if kind == "full_analysis":
            return "full"
        if kind == "analyze_strategy":
            return "strategy"
        return "default"

    async def enqueue(
        self,
        kind: JobKind,
        payload: dict[str, Any],
        *,
        parent_job_id: str | None = None,
        reanalysis_of: str | None = None,
    ) -> str:
        job_id = self.store.create(
            kind,
            payload,
            parent_job_id=parent_job_id,
            reanalysis_of=reanalysis_of,
        )
        group = self._group(kind)
        if group not in self._queues:
            self._queues[group] = asyncio.Queue()
        await self._queues[group].put(job_id)
        log.info(
            "job_enqueued",
            job_id=job_id,
            kind=kind,
            group=group,
            reanalysis_of=reanalysis_of,
        )
        return job_id

    async def start(self) -> None:
        for group, limit in self.limits.items():
            for i in range(limit):
                t = asyncio.create_task(
                    self._worker_loop(group, i), name=f"worker-{group}-{i}"
                )
                self._workers.append(t)
        log.info("queue_started", workers=len(self._workers), limits=self.limits)

    async def stop(self) -> None:
        self._stopped = True
        for t in self._workers:
            t.cancel()
        for t in self._workers:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._workers.clear()
        log.info("queue_stopped")

    async def _worker_loop(self, group: str, worker_idx: int) -> None:
        queue = self._queues[group]
        while not self._stopped:
            try:
                job_id = await queue.get()
            except asyncio.CancelledError:
                return
            job = self.store.get(job_id)
            if job is None:
                continue
            kind = job["kind"]
            handler = self.handlers.get(kind)
            if handler is None:
                self.store.mark_failed(job_id, f"no_handler_for_kind:{kind}")
                continue
            self._active[group] = self._active.get(group, 0) + 1
            self.store.mark_running(job_id)
            log.info("job_started", job_id=job_id, kind=kind, group=group)
            try:
                result = await handler(job_id, job["payload"])
                self.store.mark_done(job_id, result)
                log.info("job_done", job_id=job_id, kind=kind)
            except asyncio.CancelledError:
                self.store.mark_failed(job_id, "cancelled")
                raise
            except Exception as e:
                self.store.mark_failed(job_id, f"{type(e).__name__}: {e}")
                log.exception("job_failed", job_id=job_id, kind=kind)
            finally:
                self._active[group] = max(0, self._active.get(group, 0) - 1)

    def stats(self) -> dict[str, Any]:
        return {
            "queue_depth": sum(q.qsize() for q in self._queues.values()),
            "active_jobs": sum(self._active.values()),
            "per_group": {
                g: {
                    "queued": self._queues[g].qsize(),
                    "active": self._active.get(g, 0),
                    "limit": self.limits.get(g, 0),
                }
                for g in self._queues
            },
        }
