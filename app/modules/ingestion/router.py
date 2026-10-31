"""Các tuyến API ingestion."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.core.exceptions import AppException
from app.modules.ingestion.execution import IngestionExecutionService, authorize
from app.modules.ingestion.schemas import IngestionJobRead, IngestionStart
from app.repositories.ingestion_repo import IngestionRepository

if TYPE_CHECKING:
    from app.modules.ingestion.models import IngestionJob

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

DB = Annotated[AsyncSession, Depends(get_db)]
User = Annotated[dict[str, Any], Depends(get_current_user)]


@router.post("/jobs", response_model=IngestionJobRead, status_code=202)
async def start_ingestion(request: IngestionStart, db: DB, user: User) -> IngestionJob:
    try:
        return await IngestionExecutionService(IngestionRepository(db)).start(request, user)
    except SQLAlchemyError:
        await db.rollback()
        raise AppException(
            "Không thể lưu job ingestion", code="INGESTION_QUEUE_UNAVAILABLE", status_code=503
        ) from None


@router.get("/jobs", response_model=list[IngestionJobRead])
async def list_jobs(
    db: DB,
    user: User,
    data_source_id: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[IngestionJob]:
    authorize(user)
    return await IngestionRepository(db).list(data_source_id, limit, offset)


@router.get("/jobs/{job_id}", response_model=IngestionJobRead)
async def get_job(job_id: int, db: DB, user: User) -> IngestionJob:
    authorize(user)
    return await IngestionRepository(db).get(job_id)
