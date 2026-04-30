from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl

from auth import require_worker_token
from state import state

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    dependencies=[Depends(require_worker_token)],
)


Platform = Literal["instagram", "tiktok", "youtube_shorts"]
Quality = Literal["720p", "audio_only"]


class DownloadReq(BaseModel):
    url: HttpUrl
    platform: Platform
    quality: Quality = Field(default="720p")
    cache_key: str | None = Field(
        default=None,
        description="Опц. ключ для дедупа (например 'ig:abc123'). При совпадении файла на диске возвращается без скачивания.",
    )


@router.post("/download", status_code=202)
async def post_download(req: DownloadReq):
    payload = req.model_dump(mode="json")
    job_id = await state.queue.enqueue("download", payload)
    return {"job_id": job_id, "status": "queued"}


@router.get("/{job_id}")
async def get_job(job_id: str):
    job = state.job_store.get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="job_not_found")
    return job


@router.get("", include_in_schema=False)
async def list_jobs(limit: int = 50):
    return state.job_store.list_recent(limit=limit)
