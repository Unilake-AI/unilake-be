"""Chọn bộ dữ liệu khi tải file và đăng ký bộ dữ liệu Bronze mới."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_role
from app.core.exceptions import AppException
from app.core.roles import DATA_WRITE_ROLES, UPLOAD_STAFF_DOMAINS, RoleCode
from app.dependencies import get_db
from app.modules.datasources.models import DataLayer, Dataset, SourceSystem
from app.modules.datasources.schemas import DatasetRead, SourceSystemRead

router = APIRouter(tags=["datasets"])
DB = Annotated[AsyncSession, Depends(get_db)]
User = Annotated[dict[str, Any], Depends(get_current_user)]
Admin = Annotated[dict[str, Any], Depends(require_role(RoleCode.ADMIN, RoleCode.DATA_ADMIN))]


class DatasetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dataset_code: str = Field(min_length=1, max_length=100)
    dataset_name: str = Field(min_length=1, max_length=200)
    domain: Literal["hr", "academic", "admissions"]
    source_system_id: int = Field(gt=0)
    description: str | None = Field(default=None, max_length=4000)


@router.get("/source-systems", response_model=list[SourceSystemRead], summary="Chọn hệ thống nguồn")
async def list_source_systems(db: DB, _admin: Admin) -> list[SourceSystem]:
    return list(await db.scalars(select(SourceSystem).where(SourceSystem.is_active.is_(True))))


@router.get("/datasets", response_model=list[DatasetRead], summary="Chọn bộ dữ liệu để tải file")
async def list_datasets(
    db: DB,
    user: User,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Dataset]:
    roles = set(user.get("roles", []))
    domains = [domain for role, domain in UPLOAD_STAFF_DOMAINS.items() if role in roles]
    is_admin = not DATA_WRITE_ROLES.isdisjoint(roles)
    if not is_admin and not domains:
        raise AppException("Không có quyền chọn bộ dữ liệu", code="FORBIDDEN", status_code=403)
    query = (
        select(Dataset)
        .join(DataLayer, DataLayer.data_layer_id == Dataset.data_layer_id)
        .join(SourceSystem, SourceSystem.source_system_id == Dataset.source_system_id)
        .where(
            Dataset.status == "ACTIVE",
            DataLayer.layer_code == "BRONZE",
            SourceSystem.is_active.is_(True),
        )
    )
    if not is_admin:
        query = query.where(Dataset.domain.in_(domains))
    return list(await db.scalars(query.order_by(Dataset.dataset_id).limit(limit).offset(offset)))


@router.post(
    "/datasets", response_model=DatasetRead, status_code=201, summary="Đăng ký bộ dữ liệu Bronze"
)
async def create_dataset(request: DatasetCreate, db: DB, _admin: Admin) -> Dataset:
    source = await db.get(SourceSystem, request.source_system_id)
    layer = await db.scalar(select(DataLayer).where(DataLayer.layer_code == "BRONZE"))
    if source is None or not source.is_active or layer is None:
        raise AppException(
            "Hệ thống nguồn hoặc tầng Bronze chưa sẵn sàng",
            code="DATASET_SOURCE_INVALID",
            status_code=422,
        )
    dataset = Dataset(
        **request.model_dump(),
        data_layer_id=layer.data_layer_id,
        owner_department_id=source.owner_department_id,
        status="ACTIVE",
    )
    db.add(dataset)
    try:
        await db.commit()
        await db.refresh(dataset)
    except IntegrityError:
        await db.rollback()
        raise AppException(
            "Mã bộ dữ liệu đã tồn tại hoặc thông tin không hợp lệ",
            code="DATASET_CONFLICT",
            status_code=409,
        ) from None
    return dataset
