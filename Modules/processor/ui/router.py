import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from auth import require_admin_token
from state import state

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_token)],
)


_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._\- ]{1,200}$")


def _downloads_dir() -> Path:
    return state.settings.media_dir / "downloads"


def _is_inside(p: Path, parent: Path) -> bool:
    try:
        p.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


@router.get("/files")
async def list_files():
    d = _downloads_dir()
    d.mkdir(parents=True, exist_ok=True)
    items = []
    for f in sorted(d.iterdir()):
        if not f.is_file():
            continue
        stat = f.stat()
        items.append({
            "name": f.name,
            "size_bytes": stat.st_size,
            "modified_at": stat.st_mtime,
            "file_path": str(f),
        })
    return {"downloads_dir": str(d), "items": items}


@router.post("/files/upload", status_code=201)
async def upload_file(file: UploadFile):
    name = file.filename or "upload.bin"
    if not _SAFE_NAME_RE.match(name):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="invalid_filename (allowed: A-Z a-z 0-9 . _ - space)",
        )
    d = _downloads_dir()
    d.mkdir(parents=True, exist_ok=True)
    target = d / name
    if not _is_inside(target, d):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="path_escape")

    data = await file.read()
    target.write_bytes(data)
    return {
        "name": target.name,
        "size_bytes": target.stat().st_size,
        "file_path": str(target),
    }


@router.delete("/files/{name}", status_code=204)
async def delete_file(name: str):
    if not _SAFE_NAME_RE.match(name):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_filename")
    target = _downloads_dir() / name
    if not _is_inside(target, _downloads_dir()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="path_escape")
    if not target.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="file_not_found")
    target.unlink()
    return None


@router.get("/files/{name}/download")
async def download_file(name: str):
    if not _SAFE_NAME_RE.match(name):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_filename")
    target = _downloads_dir() / name
    if not target.exists() or not _is_inside(target, _downloads_dir()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="file_not_found")
    return FileResponse(target)


@router.get("/frames/{job_id}/{frame_name}")
async def get_frame(job_id: str, frame_name: str):
    """Отдаёт файл кадра для отображения в UI."""
    if not re.match(r"^[A-Za-z0-9_\-]{1,64}$", job_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_job_id")
    if not re.match(r"^frame_\d{3}\.jpg$", frame_name):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="invalid_frame_name")
    frames_root = state.settings.media_dir / "frames"
    target = frames_root / job_id / frame_name
    if not target.exists() or not _is_inside(target, frames_root):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="frame_not_found")
    return FileResponse(target)
