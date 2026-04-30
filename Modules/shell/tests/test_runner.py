"""RunRunner с моками downloader/processor — без сетевых вызовов.

Pipeline: download → (transcribe ∥ vision) → done.
"""
import asyncio
from pathlib import Path

import pytest

from orchestrator.config import OrchestratorSettings
from orchestrator.runs.runner import RunRunner
from orchestrator.runs.store import RunStore


class FakeDownloader:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.submitted: list[dict] = []
        self.deleted: list[str] = []

    async def submit(self, *, url, platform, quality="720p", cache_key=None):
        self.submitted.append(
            {"url": url, "platform": platform, "quality": quality, "cache_key": cache_key}
        )
        return "dl-job-1"

    async def wait_done(self, job_id, *, timeout_sec=300):
        if self.fail:
            from orchestrator.clients.downloader import DownloaderError
            raise DownloaderError("simulated_failure")
        return {
            "status": "done",
            "result": {
                "file_path": "/media/downloads/stub_xxx.mp4",
                "sha256": "deadbeef" * 8,
                "size_bytes": 4930125,
                "duration_sec": 45.5,
                "strategy_used": "stub",
            },
        }

    async def delete_file(self, job_id):
        self.deleted.append(job_id)


class FakeProcessor:
    """Mocks transcribe, vision-analyze and analyze-strategy jobs.

    Returns realistic processor-shaped results with segments, frames, sections.
    """
    def __init__(
        self,
        *,
        transcribe_fail: bool = False,
        vision_fail: bool = False,
        strategy_fail: bool = False,
    ):
        self.transcribe_fail = transcribe_fail
        self.vision_fail = vision_fail
        self.strategy_fail = strategy_fail
        self.transcribe_submitted: list[dict] = []
        self.vision_submitted: list[dict] = []
        self.strategy_submitted: list[dict] = []

    async def submit_transcribe(self, *, file_path, cache_key=None, source_ref=None):
        self.transcribe_submitted.append(
            {"file_path": file_path, "cache_key": cache_key, "source_ref": source_ref}
        )
        return "tr-job-1"

    async def submit_vision_analyze(self, *, file_path, cache_key=None, source_ref=None):
        self.vision_submitted.append(
            {"file_path": file_path, "cache_key": cache_key, "source_ref": source_ref}
        )
        return "vi-job-1"

    async def submit_analyze_strategy(self, *, transcript_text, vision_analysis=None,
                                       cache_key=None, source_ref=None):
        self.strategy_submitted.append({
            "transcript_text": transcript_text,
            "vision_analysis": vision_analysis,
            "cache_key": cache_key,
            "source_ref": source_ref,
        })
        return "st-job-1"

    async def wait_done(self, job_id, *, timeout_sec=300):
        if job_id == "tr-job-1":
            if self.transcribe_fail:
                from orchestrator.clients.processor import ProcessorError
                raise ProcessorError("simulated_transcribe_failure")
            return {
                "status": "done",
                "result": {
                    "transcript": {
                        "text": "Привет, это тестовый ролик о маркетинге.",
                        "language": "ru",
                        "provider": "openai_whisper",
                        "model": "whisper-1",
                        "duration_sec": 45.5,
                        "segments": [
                            {"start": 0.0, "end": 1.8, "text": "Привет,"},
                            {"start": 1.8, "end": 4.0, "text": "это тестовый ролик"},
                            {"start": 4.0, "end": 5.2, "text": "о маркетинге."},
                        ],
                    },
                    "cost_usd": {"transcription": 0.012},
                },
            }
        if job_id == "vi-job-1":
            if self.vision_fail:
                from orchestrator.clients.processor import ProcessorError
                raise ProcessorError("simulated_vision_failure")
            return {
                "status": "done",
                "result": {
                    "frames": {
                        "extracted": [
                            {"index": 1, "timestamp_sec": 0.0,
                             "file_path": "/media/frames/abc123/frame_001.jpg",
                             "diff_ratio": 1.0},
                            {"index": 2, "timestamp_sec": 3.2,
                             "file_path": "/media/frames/abc123/frame_002.jpg",
                             "diff_ratio": 0.42},
                        ],
                        "stats": {"raw_count": 2, "kept_count": 2},
                    },
                    "vision": {
                        "provider": "anthropic_claude",
                        "model": "claude-sonnet-4-6",
                        "prompt_template": "default",
                        "prompt_version": "vision_default:v1",
                        "scenes": [{"text": "Hello world"}],  # raw_json content
                        "input_tokens": 8200,
                        "output_tokens": 1450,
                        "latency_ms": 3240,
                    },
                    "cost_usd": {"vision": 0.024},
                },
            }
        if job_id == "st-job-1":
            if self.strategy_fail:
                from orchestrator.clients.processor import ProcessorError
                raise ProcessorError("simulated_strategy_failure")
            return {
                "status": "done",
                "result": {
                    "sections": [
                        {"id": "why",       "title": "Почему",   "body": "Хук цепляет за 1.2с"},
                        {"id": "audience",  "title": "ЦА",       "body": "28-42, родители"},
                        {"id": "triggers",  "title": "Триггеры", "body": "Контраст карьера-дом"},
                        {"id": "windows",   "title": "Окна",     "body": "Будни 6-8, 19-21"},
                        {"id": "recipe",    "title": "Рецепт",   "body": "3 детали → абстракция"},
                    ],
                    "provider": "anthropic_claude_text",
                    "model": "claude-sonnet-4-6",
                    "prompt_version": "strategy_v1",
                    "cost_usd": {"strategy": 0.045},
                },
            }
        raise AssertionError(f"unexpected job_id: {job_id}")


def _make_runner(tmp_path: Path, *, dl_fail=False, transcribe_fail=False,
                 vision_fail=False, strategy_fail=False):
    settings = OrchestratorSettings(
        db_dir=tmp_path,
        downloader_url="http://x",
        processor_url="http://x",
        orchestrator_run_timeout_sec=10,
        orchestrator_poll_interval_sec=0.01,
    )
    settings.ensure_dirs()
    store = RunStore(tmp_path / "runs.db")
    dl = FakeDownloader(fail=dl_fail)
    proc = FakeProcessor(
        transcribe_fail=transcribe_fail, vision_fail=vision_fail,
        strategy_fail=strategy_fail,
    )
    runner = RunRunner(settings, store, dl, proc)
    return runner, store, dl, proc


async def _wait_terminal(store: RunStore, run_id: str, timeout: float = 5.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        run = store.get(run_id)
        if run and run["status"] in ("done", "failed"):
            return run
        await asyncio.sleep(0.02)
    raise AssertionError(f"run {run_id} did not finish in {timeout}s")


@pytest.mark.asyncio
async def test_happy_path_done(tmp_path: Path):
    """download → transcribe ∥ vision → done. Файл удаляется в конце."""
    runner, store, dl, proc = _make_runner(tmp_path)
    rid = store.create(
        url="https://www.instagram.com/reel/abc/",
        platform="instagram",
        external_id="abc",
    )
    runner.kick_off(rid)
    run = await _wait_terminal(store, rid)

    assert run["status"] == "done"
    assert dl.deleted == ["dl-job-1"]

    # Cache-key пробрасывается в обе processor-задачи
    assert proc.transcribe_submitted[0]["cache_key"] == "instagram:abc"
    assert proc.vision_submitted[0]["cache_key"] == "instagram:abc"
    assert proc.transcribe_submitted[0]["source_ref"] == {
        "platform": "instagram", "external_id": "abc",
    }


@pytest.mark.asyncio
async def test_transcribe_step_saves_full_text_and_segments(tmp_path: Path):
    """_step_transcribe сохраняет text, segments[], language, provider, model."""
    runner, store, dl, proc = _make_runner(tmp_path)
    rid = store.create(url="https://x", platform="instagram")
    runner.kick_off(rid)
    run = await _wait_terminal(store, rid)

    tr = run["steps"]["transcribe"]
    assert tr["status"] == "done"
    assert tr["text"] == "Привет, это тестовый ролик о маркетинге."
    assert tr["transcript_preview"] == "Привет, это тестовый ролик о маркетинге."
    assert tr["language"] == "ru"
    assert tr["provider"] == "openai_whisper"
    assert tr["model"] == "whisper-1"
    assert tr["words_count"] == 6

    # Segments: timestamped chunks
    assert len(tr["segments"]) == 3
    assert tr["segments"][0] == {"start": 0.0, "end": 1.8, "text": "Привет,"}
    assert tr["segments"][2] == {"start": 4.0, "end": 5.2, "text": "о маркетинге."}


@pytest.mark.asyncio
async def test_vision_step_saves_frames_with_thumb_urls(tmp_path: Path):
    """_step_vision сохраняет полный frames[] с URL-ами вместо file_path."""
    runner, store, dl, proc = _make_runner(tmp_path)
    rid = store.create(url="https://x", platform="instagram")
    runner.kick_off(rid)
    run = await _wait_terminal(store, rid)

    vi = run["steps"]["vision"]
    assert vi["status"] == "done"
    assert vi["frames_count"] == 2
    assert vi["provider"] == "anthropic_claude"
    assert vi["model"] == "claude-sonnet-4-6"
    assert vi["prompt_version"] == "vision_default:v1"

    # Frames array: file_path translated to public URL + per-frame analysis fields
    assert len(vi["frames"]) == 2
    f0 = vi["frames"][0]
    assert f0["index"] == 1
    assert f0["timestamp_sec"] == 0.0
    assert f0["diff_ratio"] == 1.0
    assert f0["thumb_url"] == "/api/media/frames/abc123/frame_001.jpg"
    # Per-frame analysis fields exist (FakeProcessor не возвращает frames_analysis,
    # поэтому defaults: scene_type=None, text/visual='', objects=[])
    assert "scene_type" in f0 and "text_on_screen" in f0 and "visual" in f0 and "objects" in f0
    assert vi["frames"][1]["thumb_url"] == "/api/media/frames/abc123/frame_002.jpg"

    # Analysis block (raw_json from Claude) preserved without provider/model dup
    assert vi["analysis"] == {"scenes": [{"text": "Hello world"}]}


@pytest.mark.asyncio
async def test_download_failure(tmp_path: Path):
    runner, store, dl, proc = _make_runner(tmp_path, dl_fail=True)
    rid = store.create(url="https://x", platform="tiktok")
    runner.kick_off(rid)
    run = await _wait_terminal(store, rid)

    assert run["status"] == "failed"
    assert "simulated_failure" in run["error"]
    assert run["steps"]["download"]["status"] == "failed"
    assert proc.transcribe_submitted == []
    assert proc.vision_submitted == []


@pytest.mark.asyncio
async def test_vision_merges_per_frame_analysis_by_index(tmp_path: Path):
    """frames_analysis из vision LLM должен сливаться с frames[] по index."""
    runner, store, dl, proc = _make_runner(tmp_path)
    # Расширяем mock: vi-job-1 возвращает frames_analysis
    orig_wait = proc.wait_done
    async def custom_wait(job_id, *, timeout_sec=300):
        if job_id == "vi-job-1":
            return {
                "status": "done",
                "result": {
                    "frames": {
                        "extracted": [
                            {"index": 1, "timestamp_sec": 0.0,
                             "file_path": "/media/frames/abc/frame_001.jpg",
                             "diff_ratio": 1.0},
                            {"index": 2, "timestamp_sec": 3.0,
                             "file_path": "/media/frames/abc/frame_002.jpg",
                             "diff_ratio": 0.4},
                        ],
                        "stats": {"raw_count": 2, "kept_count": 2},
                    },
                    "vision": {
                        "provider": "anthropic_claude", "model": "claude-sonnet-4-6",
                        "prompt_version": "vision_default:v2",
                        "frames_analysis": [
                            {"index": 1, "scene_type": "talking_head",
                             "text_on_screen": "Привет",
                             "visual": "Молодой человек в кадре",
                             "objects": ["микрофон", "стол"]},
                            {"index": 2, "scene_type": "cutaway",
                             "text_on_screen": "",
                             "visual": "Крупный план рук",
                             "objects": ["руки"]},
                        ],
                    },
                    "cost_usd": {"vision": 0.03},
                },
            }
        return await orig_wait(job_id, timeout_sec=timeout_sec)
    proc.wait_done = custom_wait

    rid = store.create(url="https://x", platform="instagram")
    runner.kick_off(rid)
    run = await _wait_terminal(store, rid)

    assert run["status"] == "done"
    frames = run["steps"]["vision"]["frames"]
    assert frames[0]["scene_type"] == "talking_head"
    assert frames[0]["text_on_screen"] == "Привет"
    assert frames[0]["visual"] == "Молодой человек в кадре"
    assert frames[0]["objects"] == ["микрофон", "стол"]
    assert frames[1]["scene_type"] == "cutaway"
    assert frames[1]["text_on_screen"] == ""
    assert frames[1]["objects"] == ["руки"]


@pytest.mark.asyncio
async def test_strategy_step_runs_after_transcribe_and_vision(tmp_path: Path):
    """После успешного transcribe+vision — strategy запускается с обоих входов."""
    runner, store, dl, proc = _make_runner(tmp_path)
    rid = store.create(url="https://x", platform="instagram")
    runner.kick_off(rid)
    run = await _wait_terminal(store, rid)

    assert run["status"] == "done"
    st = run["steps"]["strategy"]
    assert st["status"] == "done"
    assert len(st["sections"]) == 5
    assert st["sections"][0]["id"] == "why"
    assert st["cost_usd"] == 0.045
    assert st["prompt_version"] == "strategy_v1"

    # Проверяем что strategy получил transcript+vision на вход
    call = proc.strategy_submitted[0]
    assert "тестовый ролик" in call["transcript_text"]
    assert call["vision_analysis"]["scenes"] == [{"text": "Hello world"}]


@pytest.mark.asyncio
async def test_graceful_partial_failure_transcribe(tmp_path: Path):
    """transcribe упал, vision OK → run done с partial. Strategy всё равно зовётся."""
    runner, store, dl, proc = _make_runner(tmp_path, transcribe_fail=True)
    rid = store.create(url="https://x", platform="tiktok")
    runner.kick_off(rid)
    run = await _wait_terminal(store, rid)

    assert run["status"] == "done"
    assert run["steps"]["transcribe"]["status"] == "failed"
    assert run["steps"]["vision"]["status"] == "done"
    # Strategy запустилась с пустым transcript_text но получила vision_analysis
    assert run["steps"]["strategy"]["status"] == "done"
    call = proc.strategy_submitted[0]
    assert call["transcript_text"] == "(транскрипт пустой)"
    assert call["vision_analysis"] is not None
    # Файл удаляется (graceful — diskspace важнее)
    assert dl.deleted == ["dl-job-1"]


@pytest.mark.asyncio
async def test_graceful_partial_failure_vision(tmp_path: Path):
    """vision упал, transcribe OK → run done с partial."""
    runner, store, dl, proc = _make_runner(tmp_path, vision_fail=True)
    rid = store.create(url="https://x", platform="tiktok")
    runner.kick_off(rid)
    run = await _wait_terminal(store, rid)

    assert run["status"] == "done"
    assert run["steps"]["vision"]["status"] == "failed"
    assert run["steps"]["transcribe"]["status"] == "done"
    assert run["steps"]["strategy"]["status"] == "done"


@pytest.mark.asyncio
async def test_both_transcribe_and_vision_failed_aborts_run(tmp_path: Path):
    """Оба шага упали → run failed, strategy не запускается."""
    runner, store, dl, proc = _make_runner(
        tmp_path, transcribe_fail=True, vision_fail=True,
    )
    rid = store.create(url="https://x", platform="tiktok")
    runner.kick_off(rid)
    run = await _wait_terminal(store, rid)

    assert run["status"] == "failed"
    assert "both_steps_failed" in run["error"]
    assert proc.strategy_submitted == []
    # При полном fail файл НЕ удаляется
    assert dl.deleted == []


@pytest.mark.asyncio
async def test_strategy_failure_does_not_break_run(tmp_path: Path):
    """Strategy упала — run всё равно done (graceful). Просто без sections."""
    runner, store, dl, proc = _make_runner(tmp_path, strategy_fail=True)
    rid = store.create(url="https://x", platform="instagram")
    runner.kick_off(rid)
    run = await _wait_terminal(store, rid)

    assert run["status"] == "done"
    assert run["steps"]["strategy"]["status"] == "failed"
    assert "simulated_strategy_failure" in run["steps"]["strategy"]["error"]
    # Transcribe и vision успешны
    assert run["steps"]["transcribe"]["status"] == "done"
    assert run["steps"]["vision"]["status"] == "done"


@pytest.mark.asyncio
async def test_heartbeat_pulses_updated_at_during_long_wait(tmp_path: Path):
    """Heartbeat должен обновлять updated_at пока pipeline работает.

    Симулируем долгую processor-задачу, во время которой проверяем что pulse
    действительно бьётся (updated_at моложе чем at-creation).
    """
    settings = OrchestratorSettings(
        db_dir=tmp_path,
        downloader_url="http://x",
        processor_url="http://x",
        orchestrator_run_timeout_sec=10,
        orchestrator_poll_interval_sec=0.01,
        orchestrator_heartbeat_interval_sec=1,  # быстрый heartbeat для теста
    )
    settings.ensure_dirs()
    store = RunStore(tmp_path / "runs.db")
    dl = FakeDownloader()

    class SlowProcessor(FakeProcessor):
        async def wait_done(self, job_id, *, timeout_sec=300):
            # Имитируем 2.5с медленной работы — heartbeat должен пульсировать ≥2 раза
            await asyncio.sleep(2.5)
            return await super().wait_done(job_id, timeout_sec=timeout_sec)

    proc = SlowProcessor()
    runner = RunRunner(settings, store, dl, proc)
    rid = store.create(url="https://x", platform="instagram")
    runner.kick_off(rid)

    # Ждём 1.5с — pipeline ещё в работе, но heartbeat должен пульсировать
    await asyncio.sleep(1.5)
    mid_run = store.get(rid)
    assert mid_run["status"] != "done", "тест ломается если processor стал быстрее"
    initial_updated_at = mid_run["updated_at"]

    # Ждём ещё — heartbeat должен снова пульсануть
    await asyncio.sleep(1.2)
    mid_run2 = store.get(rid)
    if mid_run2["status"] not in ("done", "failed"):
        # Pipeline всё ещё в работе — updated_at должен был измениться от heartbeat-а
        assert mid_run2["updated_at"] > initial_updated_at, \
            "heartbeat не обновил updated_at"

    # Ждём финала
    run = await _wait_terminal(store, rid, timeout=8.0)
    assert run["status"] == "done"
