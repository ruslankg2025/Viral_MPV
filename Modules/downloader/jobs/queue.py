import asyncio
from typing import Any, Awaitable, Callable

from jobs.store import JobKind, JobStore
from logging_setup import get_logger

log = get_logger("jobs.queue")

JobHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class JobQueue:
    """Очередь скачиваний с ограничением параллельности (MAX_CONCURRENT)."""

    def __init__(
        self,
        store: JobStore,
        max_concurrent: int,
        handlers: dict[str, JobHandler],
        job_timeout_sec: int = 300,
    ):
        self.store = store
        self.max_concurrent = max_concurrent
        self.handlers = handlers
        self.job_timeout_sec = job_timeout_sec
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._active: int = 0
        self._stopped = False

    async def enqueue(self, kind: JobKind, payload: dict[str, Any]) -> str:
        job_id = self.store.create(kind, payload)
        await self._queue.put(job_id)
        log.info("job_enqueued", job_id=job_id, kind=kind)
        return job_id

    async def start(self) -> None:
        for i in range(self.max_concurrent):
            t = asyncio.create_task(self._worker_loop(i), name=f"worker-{i}")
            self._workers.append(t)
        log.info("queue_started", workers=len(self._workers))

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

    async def _worker_loop(self, worker_idx: int) -> None:
        while not self._stopped:
            try:
                job_id = await self._queue.get()
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
            self._active += 1
            self.store.mark_running(job_id)
            log.info("job_started", job_id=job_id, kind=kind)
            try:
                result = await asyncio.wait_for(
                    handler(job_id, job["payload"]),
                    timeout=self.job_timeout_sec,
                )
                self.store.mark_done(job_id, result)
                log.info("job_done", job_id=job_id, kind=kind)
            except asyncio.TimeoutError:
                self.store.mark_failed(job_id, f"timeout:{self.job_timeout_sec}s")
                log.warning("job_timeout", job_id=job_id, kind=kind)
            except asyncio.CancelledError:
                self.store.mark_failed(job_id, "cancelled")
                raise
            except Exception as e:
                self.store.mark_failed(job_id, f"{type(e).__name__}: {e}")
                log.exception("job_failed", job_id=job_id, kind=kind)
            finally:
                self._active = max(0, self._active - 1)

    def stats(self) -> dict[str, Any]:
        return {
            "queue_depth": self._queue.qsize(),
            "active_jobs": self._active,
            "max_concurrent": self.max_concurrent,
        }
