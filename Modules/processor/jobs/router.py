from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth import require_worker_token
from jobs.store import JobKind
from state import state

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_worker_token)])


class SamplingOpts(BaseModel):
    fps: float = Field(default=1.0, gt=0, le=10)
    diff_threshold: float = Field(default=0.10, ge=0, le=1)
    min_frames: int = Field(default=3, ge=1, le=200)
    max_frames: int = Field(default=40, ge=1, le=500)


class SourceRefIn(BaseModel):
    """Opaque-указатель на источник (v2). Идёт в cache_key и echo в result."""

    platform: str
    external_id: str


class ProvidersIn(BaseModel):
    """Раздельный выбор провайдеров (v2). Имеет приоритет над плоским polем `provider`."""

    transcription: str | None = None
    vision: str | None = None


AnalysisProfile = Literal["quick", "standard", "deep"]


class TranscribeReq(BaseModel):
    file_path: str
    cache_key: str | None = None
    language: str | None = None
    provider: str | None = None
    # v2
    source_ref: SourceRefIn | None = None


class ExtractFramesReq(BaseModel):
    file_path: str
    sampling: SamplingOpts | None = None
    # v2
    source_ref: SourceRefIn | None = None


class VisionAnalyzeReq(BaseModel):
    file_path: str
    cache_key: str | None = None
    sampling: SamplingOpts | None = None
    prompt_template: Literal["default", "detailed", "hooks_focused"] = "default"
    provider: str | None = None
    # v2
    source_ref: SourceRefIn | None = None
    prompt_version: str | None = None
    analysis_profile: AnalysisProfile | None = None


class FullAnalysisReq(BaseModel):
    file_path: str
    cache_key: str | None = None
    sampling: SamplingOpts | None = None
    transcribe_provider: str | None = None
    vision_provider: str | None = None
    # v2
    source_ref: SourceRefIn | None = None
    prompt_version: str | None = None
    analysis_profile: AnalysisProfile | None = None
    providers: ProvidersIn | None = None
    prompt_template: Literal["default", "detailed", "hooks_focused"] | None = None


class AnalyzeStrategyReq(BaseModel):
    """Strategy/virality analysis: 5-секционный JSON разбор на основе transcript+vision."""
    transcript_text: str = Field(..., min_length=1, max_length=20_000)
    vision_analysis: dict[str, Any] | None = None
    cache_key: str | None = None
    provider: str | None = None
    source_ref: SourceRefIn | None = None


class ReanalyzeOverride(BaseModel):
    vision_model: str | None = None
    transcription_model: str | None = None
    prompt_version: str | None = None
    prompt_template: Literal["default", "detailed", "hooks_focused"] | None = None
    analysis_profile: AnalysisProfile | None = None
    sampling: SamplingOpts | None = None


class ReanalyzeReq(BaseModel):
    base_job_id: str
    override: ReanalyzeOverride | None = None


def _validate_file_path(file_path: str) -> Path:
    """Проверяет, что файл существует и лежит внутри MEDIA_DIR."""
    media = state.settings.media_dir.resolve()
    candidate = (media / file_path.lstrip("/").removeprefix("media/")).resolve()
    # Принимаем и полный путь, если он уже внутри media:
    alt = Path(file_path).resolve() if Path(file_path).is_absolute() else None
    target = alt if alt and _is_inside(alt, media) else candidate
    if not _is_inside(target, media):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="file_outside_media_dir")
    if not target.exists() or not target.is_file():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="file_not_found")
    return target


def _is_inside(p: Path, parent: Path) -> bool:
    try:
        p.relative_to(parent)
        return True
    except ValueError:
        return False


async def _submit(kind: JobKind, payload: dict[str, Any]) -> dict[str, Any]:
    # Валидация file_path до постановки в очередь
    resolved = _validate_file_path(payload["file_path"])
    payload["file_path"] = str(resolved)
    job_id = await state.queue.enqueue(kind, payload)
    return {"job_id": job_id, "status": "queued"}


@router.post("/transcribe", status_code=202)
async def post_transcribe(req: TranscribeReq):
    return await _submit("transcribe", req.model_dump())


@router.post("/extract-frames", status_code=202)
async def post_extract_frames(req: ExtractFramesReq):
    return await _submit("extract_frames", req.model_dump())


@router.post("/vision-analyze", status_code=202)
async def post_vision_analyze(req: VisionAnalyzeReq):
    return await _submit("vision_analyze", req.model_dump())


@router.post("/full-analysis", status_code=202)
async def post_full_analysis(req: FullAnalysisReq):
    return await _submit("full_analysis", req.model_dump())


@router.post("/analyze-strategy", status_code=202)
async def post_analyze_strategy(req: AnalyzeStrategyReq):
    """Strategy job — НЕ требует file_path в media (input — text + dict).
    Кладёт в очередь напрямую, минуя _validate_file_path.
    """
    payload = req.model_dump()
    job_id = await state.queue.enqueue("analyze_strategy", payload)
    return {"job_id": job_id, "status": "queued"}


@router.post("/reanalyze", status_code=202)
async def post_reanalyze(req: ReanalyzeReq):
    """Re-analyze v2: запускает новый full_analysis на основе исходного job-а
    с применением override (новая модель / промпт / профиль).

    Исходный job не модифицируется — создаётся новый job со ссылкой
    `reanalysis_of = base_job_id`.
    """
    base = state.job_store.get(req.base_job_id)
    if base is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="base_job_not_found")
    if base["status"] != "done":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"base_job_not_done: status={base['status']}",
        )

    base_payload = dict(base["payload"] or {})
    override = req.override.model_dump(exclude_none=True) if req.override else {}

    # Применяем override к исходному payload
    new_payload = dict(base_payload)
    if "vision_model" in override:
        new_payload["vision_provider"] = override["vision_model"]
    if "transcription_model" in override:
        new_payload["transcribe_provider"] = override["transcription_model"]
    if "prompt_version" in override:
        new_payload["prompt_version"] = override["prompt_version"]
    if "prompt_template" in override:
        new_payload["prompt_template"] = override["prompt_template"]
    if "analysis_profile" in override:
        new_payload["analysis_profile"] = override["analysis_profile"]
    if "sampling" in override:
        new_payload["sampling"] = override["sampling"]

    # cache_key исходного job-а сохраняется, но build_cache_key учтёт новые поля
    # и даст другой effective key — повторного попадания в кеш не будет.

    # Валидируем file_path ещё раз (на случай если файл удалили)
    _validate_file_path(new_payload["file_path"])

    job_id = await state.queue.enqueue(
        "full_analysis",
        new_payload,
        parent_job_id=req.base_job_id,
        reanalysis_of=req.base_job_id,
    )
    return {"job_id": job_id, "status": "queued", "reanalysis_of": req.base_job_id}


@router.get("/{job_id}")
async def get_job(job_id: str):
    job = state.job_store.get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="job_not_found")
    return job


@router.get("", include_in_schema=False)
async def list_jobs(limit: int = 50):
    return state.job_store.list_recent(limit=limit)
