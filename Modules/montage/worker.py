"""Один воркер очереди автомонтажа: строго 1 джоб за раз.

Rule Zero: рендер выполняет `orchestrator.py` (детерминированный оркестратор
поверх инструментов OpenMontage) — воркер лишь ставит его субпроцессом с
предохранителями: ночное окно + проверка свободной памяти всей машины + жёсткий
таймаут (убиваем зависший/своп-затык → dead-letter). Субпроцесс, а не поток:
память полностью освобождается по завершении джоба (критично на 8ГБ-дроплете),
и его можно убить по таймауту. См. [[montage-build]].
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import Settings
from logging_setup import get_logger
from storage import JobStore

log = get_logger()

_ORCHESTRATOR = Path(__file__).resolve().parent / "orchestrator.py"
_LOG_TAIL_CHARS = 4000


def mem_available_mb() -> int:
    """MemAvailable всей машины (МБ) из /proc/meminfo. -1 если недоступно."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024  # kB → MB
    except Exception as e:  # noqa: BLE001
        log.warning("meminfo_unreadable", error=str(e))
    return -1


def in_night_window(settings: Settings, now: datetime | None = None) -> bool:
    """Локальное время контейнера (TZ) в окне [start, end). Поддерживает переход
    через полночь (напр. 22→6). Окно выключено → всегда True."""
    if not settings.night_enabled:
        return True
    h = (now or datetime.now()).hour
    start, end = settings.night_start_hour, settings.night_end_hour
    if start == end:
        return True
    if start < end:
        return start <= h < end
    return h >= start or h < end  # wrap через полночь


def _gate_reason(settings: Settings, job: dict) -> str | None:
    """Почему джоб НЕ стартует сейчас (None → можно стартовать)."""
    avail = mem_available_mb()
    if 0 <= avail < settings.min_avail_mb:
        return f"low_memory: avail={avail}MB < {settings.min_avail_mb}MB"
    if not job.get("force") and not in_night_window(settings):
        return "outside_night_window"
    return None


async def _run_job(settings: Settings, store: JobStore, job: dict) -> None:
    jid = job["id"]
    out_dir = job["out_dir"]
    log_path = Path(out_dir) / "run.log"
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, str(_ORCHESTRATOR),
        "--source", job["source_path"],
        "--out-dir", out_dir,
        "--model", job.get("model_size") or settings.default_model,
        "--width", str(job.get("width") or settings.default_width),
        "--height", str(job.get("height") or settings.default_height),
    ]
    if job.get("language"):
        cmd += ["--language", job["language"]]

    env = dict(os.environ)
    env["OPENMONTAGE_ROOT"] = str(settings.openmontage_root)
    env["HF_HOME"] = str(settings.work_dir / "hf")          # кэш моделей whisper
    threads = str(max(1, settings.cpu_threads))
    env.update(OMP_NUM_THREADS=threads, OPENBLAS_NUM_THREADS=threads,
               MKL_NUM_THREADS=threads, NUMEXPR_NUM_THREADS=threads,
               CT2_INTER_THREADS="1", CT2_INTRA_THREADS=threads)

    store.mark_running(jid)
    log.info("job_start", job_id=jid, model=cmd[cmd.index("--model") + 1],
             wh=f"{job.get('width')}x{job.get('height')}", avail_mb=mem_available_mb())
    t0 = time.monotonic()

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env,
    )
    timed_out = False
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=settings.job_timeout_sec)
    except asyncio.TimeoutError:
        timed_out = True
        proc.kill()
        out, _ = await proc.communicate()
    dur = time.monotonic() - t0

    raw = (out or b"").decode("utf-8", "replace")
    try:
        log_path.write_text(raw, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    tail = raw[-_LOG_TAIL_CHARS:]

    if timed_out:
        store.finish(jid, status="failed", error_message=f"timeout>{settings.job_timeout_sec}s",
                     log_tail=tail, duration_s=round(dur, 1))
        log.warning("job_timeout", job_id=jid, duration_s=round(dur, 1))
        return

    report = _read_report(out_dir)
    rc = proc.returncode
    if rc == 0 and report and report.get("master"):
        store.finish(jid, status="done", result_path=report["master"],
                     qc_passed=report.get("qc_passed"), qc_issues=report.get("qc_issues"),
                     log_tail=tail, duration_s=round(dur, 1))
        log.info("job_done", job_id=jid, qc_passed=report.get("qc_passed"),
                 duration_s=round(dur, 1))
    else:
        err = (report or {}).get("error") or f"orchestrator_exit_{rc}"
        store.finish(jid, status="failed", error_message=str(err)[:500],
                     log_tail=tail, duration_s=round(dur, 1))
        log.warning("job_failed", job_id=jid, rc=rc, error=str(err)[:200])


def _read_report(out_dir: str) -> dict | None:
    import json
    p = Path(out_dir) / "renders" / "report.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _cleanup_old(settings: Settings, store: JobStore) -> None:
    """Удаляем каталоги завершённых джобов старше TTL (cap диска) + подметаем
    осиротевшие файлы в incoming (аплоад упал до создания джоба)."""
    import shutil
    before = (datetime.now(timezone.utc) - timedelta(hours=settings.result_ttl_hours)).isoformat()
    for job in store.old_finished(before_iso=before, limit=100):
        d = job.get("out_dir")
        # каталог джоба = родитель out_dir (jobs/<id>/), содержит и src, и out
        job_root = settings.jobs_dir / job["id"]
        try:
            if job_root.is_dir():
                shutil.rmtree(job_root, ignore_errors=True)
            elif d and Path(d).is_dir():
                shutil.rmtree(d, ignore_errors=True)
            store.delete(job["id"])
            log.info("job_cleaned", job_id=job["id"])
        except Exception as e:  # noqa: BLE001
            log.warning("job_cleanup_failed", job_id=job["id"], error=str(e))

    # осиротевший incoming (from-path переносит файл; остаётся только мусор)
    cutoff = time.time() - settings.result_ttl_hours * 3600
    try:
        for f in settings.incoming_dir.glob("*"):
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
                log.info("incoming_swept", file=f.name)
    except Exception as e:  # noqa: BLE001
        log.warning("incoming_sweep_failed", error=str(e))


async def run_worker(store: JobStore, settings: Settings) -> None:
    """Вечный цикл: один джоб за раз, с предохранителями. Отменяется на shutdown."""
    n = store.reset_orphans()
    if n:
        log.warning("orphan_jobs_reset", count=n)
    last_cleanup = 0.0
    log.info("worker_started", night=[settings.night_start_hour, settings.night_end_hour],
             min_avail_mb=settings.min_avail_mb, timeout_s=settings.job_timeout_sec)
    while True:
        try:
            now = time.monotonic()
            if now - last_cleanup > 3600:  # автоочистка раз в час
                _cleanup_old(settings, store)
                last_cleanup = now

            job = store.next_runnable(in_window=in_night_window(settings))
            if job is None:
                await asyncio.sleep(settings.poll_interval_sec)
                continue
            reason = _gate_reason(settings, job)
            if reason:
                log.info("job_gated", job_id=job["id"], reason=reason)
                await asyncio.sleep(settings.poll_interval_sec)
                continue
            await _run_job(settings, store, job)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — воркер не должен падать
            log.error("worker_loop_error", error=str(e))
            await asyncio.sleep(settings.poll_interval_sec)
