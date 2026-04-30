from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

from auth import require_worker_token
from logging_setup import get_logger
from state import state

log = get_logger("files_router")

router = APIRouter(
    prefix="/files",
    tags=["files"],
    dependencies=[Depends(require_worker_token)],
)


def _is_inside(p: Path, parent: Path) -> bool:
    try:
        p.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


@router.delete("/{job_id}", status_code=204)
async def delete_file(job_id: str):
    """Удаляет mp4-файл, созданный конкретным download-job-ом.

    Идемпотентно: если файла уже нет, возвращает 204 без ошибки.
    Запись в jobs.db остаётся как аудит.
    """
    job = state.job_store.get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="job_not_found")

    result = job.get("result") or {}
    file_path = result.get("file_path")
    if not file_path:
        log.info("delete_file_no_path", job_id=job_id)
        return

    target = Path(file_path)
    media_dir = state.settings.media_dir.resolve()
    if not _is_inside(target, media_dir):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="file_outside_media_dir"
        )

    if target.exists():
        try:
            target.unlink()
            log.info("file_deleted", job_id=job_id, file=str(target))
        except OSError as e:
            log.warning("file_delete_failed", job_id=job_id, error=str(e))
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"unlink_failed: {e}",
            )
    else:
        log.info("file_already_gone", job_id=job_id, file=str(target))
