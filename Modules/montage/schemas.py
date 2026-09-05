"""Pydantic-схемы ответов montage. Создание джоба — multipart (см. router)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class JobOut(BaseModel):
    id: str
    account_id: str | None = None
    title: str | None = None
    status: str
    source_filename: str | None = None
    model_size: str | None = None
    width: int | None = None
    height: int | None = None
    language: str | None = None
    force: bool = False
    qc_passed: bool | None = None
    qc_issues: Any = None
    error_message: str | None = None
    duration_s: float | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    # ссылки для фронта
    result_url: str | None = None
    log_url: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "JobOut":
        jid = row["id"]
        has_result = bool(row.get("result_path")) and row.get("status") == "done"
        return cls(
            id=jid,
            account_id=row.get("account_id"),
            title=row.get("title"),
            status=row["status"],
            source_filename=row.get("source_filename"),
            model_size=row.get("model_size"),
            width=row.get("width"),
            height=row.get("height"),
            language=row.get("language"),
            force=bool(row.get("force")),
            qc_passed=row.get("qc_passed"),
            qc_issues=row.get("qc_issues"),
            error_message=row.get("error_message"),
            duration_s=row.get("duration_s"),
            created_at=row["created_at"],
            started_at=row.get("started_at"),
            finished_at=row.get("finished_at"),
            result_url=f"/montage/jobs/{jid}/result" if has_result else None,
            log_url=f"/montage/jobs/{jid}/log",
        )
