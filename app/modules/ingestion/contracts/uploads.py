"""Giao diện bàn giao file đã sửa từ phân hệ chất lượng sang tiếp nhận dữ liệu."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.modules.ingestion.schemas import IngestionJobRead as IngestionJobRead
from app.modules.ingestion.uploads import UploadParser, file_type, receive_upload

if TYPE_CHECKING:
    from fastapi import UploadFile
    from sqlalchemy.ext.asyncio import AsyncSession


def safe_upload_filename(filename: str | None) -> str:
    return file_type(filename)[0]


def supported_parser_options(options: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in options.items() if key in UploadParser.model_fields}


async def receive_issue_upload(
    db: AsyncSession,
    file: UploadFile,
    dataset_id: int,
    parser_options: str,
    user: dict[str, Any],
    *,
    quality_issue_id: str,
) -> IngestionJobRead:
    job = await receive_upload(
        db, file, dataset_id, parser_options, user, quality_issue_id=quality_issue_id
    )
    return IngestionJobRead.model_validate(job)
