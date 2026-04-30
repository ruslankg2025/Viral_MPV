import asyncio
import time
from contextlib import suppress
from typing import Any

from orchestrator.clients.downloader import DownloaderClient, DownloaderError
from orchestrator.clients.processor import ProcessorClient, ProcessorError
from orchestrator.clients.profile import ProfileClient
from orchestrator.clients.script import ScriptClient
from orchestrator.config import OrchestratorSettings
from orchestrator.logging_setup import get_logger
from orchestrator.runs.store import RunStore

log = get_logger("runs.runner")

_PLATFORM_FORMAT = {
    "youtube_shorts": "shorts",
    "instagram": "reels",
    "tiktok": "reels",
}

_PLATFORM_TEMPLATE = {
    "youtube_shorts": "shorts_story_v1",
    "instagram": "reels_hook_v1",
    "tiktok": "reels_hook_v1",
}


class RunRunner:
    """Запускает pipeline для одного run-а как фоновый asyncio.task.

    Pipeline: downloading → analyzing → [generating] → done/failed.
    Шаг generating опционален: включается когда script_client is not None.
    Удаление mp4 происходит только при финальном done (после всех шагов).
    """

    def __init__(
        self,
        settings: OrchestratorSettings,
        store: RunStore,
        downloader: DownloaderClient,
        processor: ProcessorClient,
        profile: ProfileClient | None = None,
        script: ScriptClient | None = None,
    ):
        self.settings = settings
        self.store = store
        self.downloader = downloader
        self.processor = processor
        self.profile = profile
        self.script = script
        self._tasks: set[asyncio.Task] = set()

    def kick_off(self, run_id: str) -> None:
        """Создаёт фоновый task для run-а. Не ждёт его."""
        task = asyncio.create_task(
            self._guarded_run(run_id), name=f"run-{run_id[:8]}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _heartbeat(self, run_id: str) -> None:
        """Pulse updated_at каждые orchestrator_heartbeat_interval_sec.

        Не падает при исключениях — recovery-loop сам разберётся при настоящем зависании.
        """
        interval = self.settings.orchestrator_heartbeat_interval_sec
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    self.store.pulse(run_id)
                except Exception as e:
                    log.warning("heartbeat_pulse_failed", run_id=run_id, error=str(e))
        except asyncio.CancelledError:
            raise

    async def _guarded_run(self, run_id: str) -> None:
        # Heartbeat-task пульсирует updated_at пока run живёт. Это защищает
        # активные runs от ложного marking-а как stalled при рестарте shell-а.
        hb_task = asyncio.create_task(
            self._heartbeat(run_id), name=f"hb-{run_id[:8]}"
        )
        try:
            await asyncio.wait_for(
                self._run(run_id),
                timeout=self.settings.orchestrator_run_timeout_sec,
            )
        except asyncio.TimeoutError:
            self.store.set_status(
                run_id, "failed",
                error=f"run_timeout:{self.settings.orchestrator_run_timeout_sec}s",
            )
            log.warning("run_timeout", run_id=run_id)
        except asyncio.CancelledError:
            self.store.set_status(run_id, "failed", error="cancelled")
            raise
        except Exception as e:
            self.store.set_status(
                run_id, "failed", error=f"{type(e).__name__}: {e}"
            )
            log.exception("run_unhandled_error", run_id=run_id)
        finally:
            hb_task.cancel()
            with suppress(asyncio.CancelledError):
                await hb_task

    async def _run(self, run_id: str) -> None:
        run = self.store.get(run_id)
        if run is None:
            log.warning("run_missing_at_start", run_id=run_id)
            return

        url = run["url"]
        platform = run["platform"]
        external_id = run["external_id"]
        cache_key = f"{platform}:{external_id}" if external_id else None

        # Шаг 1 — download
        await self._step_download(
            run_id, url=url, platform=platform, cache_key=cache_key
        )

        run = self.store.get(run_id)
        download_step = (run["steps"] or {}).get("download", {})
        file_path = download_step.get("file_path")
        if not file_path:
            raise RuntimeError("download_step_missing_file_path")

        source_ref = (
            {"platform": platform, "external_id": external_id}
            if external_id
            else None
        )

        # Шаги 2+3 — transcribe + vision в параллель.
        # Graceful: если один шаг упал, второй имеет шанс завершиться.
        # Strategy запускается если хотя бы один из (transcribe, vision) done.
        self.store.set_status(run_id, "transcribing", current_step="transcribe")
        await asyncio.gather(
            self._step_transcribe(
                run_id,
                file_path=file_path,
                cache_key=cache_key,
                source_ref=source_ref,
            ),
            self._step_vision(
                run_id,
                file_path=file_path,
                cache_key=cache_key,
                source_ref=source_ref,
            ),
            return_exceptions=True,
        )

        # Перечитываем актуальный state шагов
        run = self.store.get(run_id)
        steps = run["steps"] or {}
        tr_done = (steps.get("transcribe") or {}).get("status") == "done"
        vi_done = (steps.get("vision") or {}).get("status") == "done"

        # Если оба шага failed — никакой strategy, run failed
        if not tr_done and not vi_done:
            tr_err = (steps.get("transcribe") or {}).get("error") or ""
            vi_err = (steps.get("vision") or {}).get("error") or ""
            err_msg = f"both_steps_failed: transcribe={tr_err} vision={vi_err}"
            self.store.set_status(run_id, "failed", error=err_msg)
            log.warning("run_failed_both_steps", run_id=run_id)
            return

        # Шаг 4 — analyze_strategy (graceful — берём то что есть)
        await self._step_analysis(
            run_id, cache_key=cache_key, source_ref=source_ref,
            transcribe_done=tr_done, vision_done=vi_done,
        )

        # Удаляем mp4 после завершения шагов (даже если partial — diskspace важнее)
        downloader_job_id = download_step.get("downloader_job_id")
        if downloader_job_id:
            try:
                await self.downloader.delete_file(downloader_job_id)
                log.info("file_deleted", run_id=run_id, job_id=downloader_job_id)
            except Exception as e:
                log.warning(
                    "file_delete_failed",
                    run_id=run_id,
                    job_id=downloader_job_id,
                    error=str(e),
                )

        # PATCH monitor (non-fatal)
        run = self.store.get(run_id)
        video_id = run["video_id"]
        if video_id:
            sha256 = download_step.get("sha256")
            await self._patch_monitor(
                run_id,
                video_id=video_id,
                sha256=sha256,
                script_id=None,
            )

        run = self.store.get(run_id)
        steps = run["steps"]
        result: dict[str, Any] = {
            "monitor_video_id": run["video_id"],
            "download": {
                "file_path": steps.get("download", {}).get("file_path"),
                "sha256": steps.get("download", {}).get("sha256"),
            },
            "transcribe": {
                "processor_job_id": steps.get("transcribe", {}).get("processor_job_id"),
                "cost_usd": steps.get("transcribe", {}).get("cost_usd"),
                "status": steps.get("transcribe", {}).get("status"),
            },
            "vision": {
                "processor_job_id": steps.get("vision", {}).get("processor_job_id"),
                "cost_usd": steps.get("vision", {}).get("cost_usd"),
                "status": steps.get("vision", {}).get("status"),
            },
            "strategy": {
                "processor_job_id": steps.get("strategy", {}).get("processor_job_id"),
                "cost_usd": steps.get("strategy", {}).get("cost_usd"),
                "status": steps.get("strategy", {}).get("status"),
            },
        }

        self.store.set_status(run_id, "done", current_step=None, result=result)
        log.info("run_done", run_id=run_id, transcribe=tr_done, vision=vi_done)

    # ---------- steps ----------

    async def _step_download(
        self, run_id: str, *, url: str, platform: str, cache_key: str | None
    ) -> None:
        self.store.set_status(run_id, "downloading", current_step="download")
        self.store.patch_step(
            run_id, "download", {"status": "running", "started_at": _ts()}
        )
        t0 = time.monotonic()
        try:
            job_id = await self.downloader.submit(
                url=url, platform=platform, cache_key=cache_key
            )
            self.store.patch_step(run_id, "download", {"downloader_job_id": job_id})
            job = await self.downloader.wait_done(
                job_id,
                timeout_sec=self.settings.orchestrator_run_timeout_sec,
            )
            res = job.get("result") or {}
            self.store.patch_step(
                run_id,
                "download",
                {
                    "status": "done",
                    "finished_at": _ts(),
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "file_path": res.get("file_path"),
                    "sha256": res.get("sha256"),
                    "size_bytes": res.get("size_bytes"),
                    "duration_sec": res.get("duration_sec"),
                    "strategy_used": res.get("strategy_used"),
                },
            )
            log.info("step_download_done", run_id=run_id, job_id=job_id)
        except DownloaderError as e:
            self.store.patch_step(
                run_id,
                "download",
                {
                    "status": "failed",
                    "finished_at": _ts(),
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "error": str(e),
                },
            )
            raise

    async def _step_transcribe(
        self,
        run_id: str,
        *,
        file_path: str,
        cache_key: str | None,
        source_ref: dict[str, str] | None,
    ) -> None:
        self.store.patch_step(
            run_id, "transcribe", {"status": "running", "started_at": _ts()}
        )
        t0 = time.monotonic()
        try:
            job_id = await self.processor.submit_transcribe(
                file_path=file_path, cache_key=cache_key, source_ref=source_ref
            )
            self.store.patch_step(run_id, "transcribe", {"processor_job_id": job_id})
            job = await self.processor.wait_done(
                job_id, timeout_sec=self.settings.orchestrator_run_timeout_sec
            )
            res = job.get("result") or {}
            transcript = res.get("transcript") or {}
            text = transcript.get("text") or ""
            segments = transcript.get("segments") or []
            words = len(text.split()) if text else 0
            cost = (res.get("cost_usd") or {}).get("transcription")
            self.store.patch_step(
                run_id,
                "transcribe",
                {
                    "status": "done",
                    "finished_at": _ts(),
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "text": text or None,
                    "transcript_preview": text[:300] if text else None,
                    "segments": segments,
                    "language": transcript.get("language"),
                    "provider": transcript.get("provider"),
                    "model": transcript.get("model"),
                    "words_count": words,
                    "cost_usd": cost,
                },
            )
            log.info("step_transcribe_done", run_id=run_id, job_id=job_id, words=words)
        except ProcessorError as e:
            self.store.patch_step(
                run_id,
                "transcribe",
                {
                    "status": "failed",
                    "finished_at": _ts(),
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "error": str(e),
                },
            )
            raise

    async def _step_vision(
        self,
        run_id: str,
        *,
        file_path: str,
        cache_key: str | None,
        source_ref: dict[str, str] | None,
    ) -> None:
        self.store.patch_step(
            run_id, "vision", {"status": "running", "started_at": _ts()}
        )
        t0 = time.monotonic()
        try:
            job_id = await self.processor.submit_vision_analyze(
                file_path=file_path, cache_key=cache_key, source_ref=source_ref
            )
            self.store.patch_step(run_id, "vision", {"processor_job_id": job_id})
            job = await self.processor.wait_done(
                job_id, timeout_sec=self.settings.orchestrator_run_timeout_sec
            )
            res = job.get("result") or {}
            frames_block = res.get("frames") or {}
            extracted = frames_block.get("extracted") or []
            stats = frames_block.get("stats") or {}
            vision_block = res.get("vision") or {}
            cost = (res.get("cost_usd") or {}).get("vision")

            # Per-frame analysis from vision LLM (vision_default v2+: frames_analysis[])
            per_frame: dict[int, dict[str, Any]] = {}
            for fa in (vision_block.get("frames_analysis") or []):
                idx = fa.get("index")
                if isinstance(idx, int):
                    per_frame[idx] = {
                        "scene_type": fa.get("scene_type"),
                        "text_on_screen": fa.get("text_on_screen") or "",
                        "visual": fa.get("visual") or "",
                        "objects": fa.get("objects") or [],
                    }

            # Translate processor file_paths to public URLs served by media router
            # AND merge per-frame LLM analysis by index.
            frames_out = []
            for f in extracted:
                fp = f.get("file_path") or ""
                parts = fp.replace("\\", "/").split("/")
                fname = parts[-1] if parts else ""
                jid = parts[-2] if len(parts) >= 2 else job_id
                thumb_url = (
                    f"/api/media/frames/{jid}/{fname}"
                    if fname.startswith("frame_") and fname.endswith(".jpg")
                    else None
                )
                idx = f.get("index")
                analysis = per_frame.get(idx, {}) if isinstance(idx, int) else {}
                frames_out.append({
                    "index": idx,
                    "timestamp_sec": f.get("timestamp_sec"),
                    "diff_ratio": f.get("diff_ratio"),
                    "thumb_url": thumb_url,
                    "scene_type": analysis.get("scene_type"),
                    "text_on_screen": analysis.get("text_on_screen") or "",
                    "visual": analysis.get("visual") or "",
                    "objects": analysis.get("objects") or [],
                })

            frames_count = stats.get("kept_count") or stats.get("raw_count") or len(extracted)

            self.store.patch_step(
                run_id,
                "vision",
                {
                    "status": "done",
                    "finished_at": _ts(),
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "frames_count": frames_count,
                    "frames": frames_out,
                    "analysis": {
                        k: v for k, v in vision_block.items()
                        if k not in ("provider", "model", "prompt_template", "prompt_version",
                                     "input_tokens", "output_tokens", "latency_ms")
                    },
                    "provider": vision_block.get("provider"),
                    "model": vision_block.get("model"),
                    "prompt_version": vision_block.get("prompt_version"),
                    "cost_usd": cost,
                },
            )
            log.info("step_vision_done", run_id=run_id, job_id=job_id, frames=frames_count)
        except ProcessorError as e:
            self.store.patch_step(
                run_id,
                "vision",
                {
                    "status": "failed",
                    "finished_at": _ts(),
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "error": str(e),
                },
            )
            raise

    async def _step_analysis(
        self, run_id: str, *,
        cache_key: str | None,
        source_ref: dict[str, str] | None,
        transcribe_done: bool,
        vision_done: bool,
    ) -> None:
        """Strategy/virality LLM-разбор. Graceful: если оба входных шага
        упали — пропускаем (skipped). Если хотя бы один done — пробуем."""
        if not transcribe_done and not vision_done:
            self.store.patch_step(run_id, "strategy", {
                "status": "skipped", "finished_at": _ts(),
                "error": "both_inputs_failed",
            })
            return

        self.store.set_status(run_id, "analyzing", current_step="strategy")
        self.store.patch_step(
            run_id, "strategy", {"status": "running", "started_at": _ts()}
        )

        run = self.store.get(run_id)
        steps = run["steps"] or {}
        transcript_text = (steps.get("transcribe") or {}).get("text") or \
                          (steps.get("transcribe") or {}).get("transcript_preview") or ""
        vision_analysis = (steps.get("vision") or {}).get("analysis") or {}

        if not transcript_text and not vision_analysis:
            # Защита: оба done но без полезных данных — пропускаем gracefully
            self.store.patch_step(run_id, "strategy", {
                "status": "skipped", "finished_at": _ts(),
                "error": "no_input_data",
            })
            return

        t0 = time.monotonic()
        try:
            job_id = await self.processor.submit_analyze_strategy(
                transcript_text=transcript_text or "(транскрипт пустой)",
                vision_analysis=vision_analysis or None,
                cache_key=cache_key,
                source_ref=source_ref,
            )
            self.store.patch_step(run_id, "strategy", {"processor_job_id": job_id})
            job = await self.processor.wait_done(
                job_id, timeout_sec=self.settings.orchestrator_run_timeout_sec
            )
            res = job.get("result") or {}
            sections = res.get("sections") or []
            cost = (res.get("cost_usd") or {}).get("strategy")
            self.store.patch_step(
                run_id, "strategy",
                {
                    "status": "done",
                    "finished_at": _ts(),
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "sections": sections,
                    "provider": res.get("provider"),
                    "model": res.get("model"),
                    "prompt_version": res.get("prompt_version"),
                    "cost_usd": cost,
                },
            )
            log.info(
                "step_analysis_done", run_id=run_id, job_id=job_id,
                sections=len(sections), cost=cost,
            )
        except ProcessorError as e:
            # Graceful: failed strategy не валит весь run, просто помечается
            self.store.patch_step(
                run_id, "strategy",
                {
                    "status": "failed",
                    "finished_at": _ts(),
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "error": str(e),
                },
            )
            log.warning("step_analysis_failed", run_id=run_id, error=str(e))
            # НЕ raise — даём run-у завершиться done с частичными данными

    async def _patch_monitor(
        self,
        run_id: str,
        *,
        video_id: str,
        sha256: str | None,
        script_id: str | None,
    ) -> None:
        from orchestrator.state import state
        from datetime import datetime, timezone

        try:
            await state.monitor_client.patch_analysis(
                video_id,
                orchestrator_run_id=run_id,
                script_id=script_id,
                sha256=sha256,
                analysis_done_at=datetime.now(timezone.utc).isoformat(),
            )
            log.info("monitor_patched", run_id=run_id, video_id=video_id)
        except Exception as e:
            log.warning("monitor_patch_failed", run_id=run_id, video_id=video_id, error=str(e))


def _ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
