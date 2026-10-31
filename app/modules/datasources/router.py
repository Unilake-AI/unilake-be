"""Các tuyến API nguồn dữ liệu."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.core.responses import COMMON_ERROR_RESPONSES
from app.modules.datasources.schemas import (
    DataSourceCreate,
    DataSourceFilter,
    DataSourceItem,
    DataSourceResponse,
    DataSourceUpdate,
)
from app.modules.datasources.service import (
    ALLOWED_DATA_SOURCE_ROLES_RESPONSE,
    ALLOWED_DATA_SOURCE_WRITE_ROLES_RESPONSE,
    DataSourceService,
)

router = APIRouter(prefix="/datasources", tags=["datasources"])

DATA_SOURCE_ERROR_RESPONSES = {
    **COMMON_ERROR_RESPONSES,
    403: {
        **COMMON_ERROR_RESPONSES[403],
        "description": (
            f"Forbidden - requires one of roles: {', '.join(ALLOWED_DATA_SOURCE_ROLES_RESPONSE)}."
        ),
    },
}

DATA_SOURCE_WRITE_ERROR_RESPONSES = {
    **COMMON_ERROR_RESPONSES,
    403: {
        **COMMON_ERROR_RESPONSES[403],
        "description": (
            "Forbidden - requires one of roles: "
            f"{', '.join(ALLOWED_DATA_SOURCE_WRITE_ROLES_RESPONSE)}."
        ),
    },
}


@router.get("", response_model=DataSourceResponse, responses=DATA_SOURCE_ERROR_RESPONSES)
async def get_data_source(
    filters: Annotated[DataSourceFilter, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> DataSourceResponse:
    """Danh sách nguồn dữ liệu có xác thực, phân quyền, filter và phân trang."""

    service = DataSourceService(db)
    return await service.get_data_source(filters, current_user)


@router.get(
    "/{data_source_id}",
    response_model=DataSourceItem,
    responses={
        **DATA_SOURCE_ERROR_RESPONSES,
        404: {
            **COMMON_ERROR_RESPONSES[404],
            "description": "Not found - data source does not exist.",
        },
    },
)
async def get_data_source_detail(
    data_source_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> DataSourceItem:
    """Chi tiết một nguồn dữ liệu theo id, có xác thực và phân quyền."""

    service = DataSourceService(db)
    return await service.get_data_source_detail(data_source_id, current_user)


@router.post("/create", response_model=DataSourceItem, responses=DATA_SOURCE_WRITE_ERROR_RESPONSES)
async def create_data_source(
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    request: Annotated[DataSourceCreate, Body()],
) -> DataSourceItem:
    """Tạo một data source mới."""
    service = DataSourceService(db)
    return await service.create_data_source(current_user, request)


@router.patch(
    "/{data_source_id}",
    response_model=DataSourceItem,
    responses={
        **DATA_SOURCE_WRITE_ERROR_RESPONSES,
        404: {
            **COMMON_ERROR_RESPONSES[404],
            "description": "Not found - data source does not exist.",
        },
    },
)
async def update_data_source(
    data_source_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    request: Annotated[DataSourceUpdate, Body()],
) -> DataSourceItem:
    """Cập nhật một data source bằng payload partial."""

    service = DataSourceService(db)
    return await service.update_data_source(current_user, data_source_id, request)
