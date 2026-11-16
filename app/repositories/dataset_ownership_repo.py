"""Quyền sở hữu dataset được lưu trong DB, tách khỏi quyền đăng nhập."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from app.core.exceptions import AppException
from app.modules.datasources.models import Dataset
from app.modules.users.models import Department

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

DOMAIN_DEPARTMENTS = {"hr": "PNS", "academic": "PDT", "admissions": "PTS"}


async def default_dataset_owner(
    db: AsyncSession, domain: str, source_owner: int | None
) -> int | None:
    if source_owner is not None:
        return source_owner
    code = DOMAIN_DEPARTMENTS.get(domain)
    if code is None:
        return None
    return await db.scalar(
        select(Department.department_id).where(
            Department.department_code == code, Department.status == "ACTIVE"
        )
    )


async def set_dataset_owner(
    db: AsyncSession, dataset_id: int, owner: int, expected: int | None
) -> Dataset:
    dataset = await db.scalar(
        select(Dataset).where(Dataset.dataset_id == dataset_id).with_for_update()
    )
    department = await db.get(Department, owner)
    if dataset is None:
        raise AppException("Không tìm thấy bộ dữ liệu", code="DATASET_NOT_FOUND", status_code=404)
    if department is None or department.status != "ACTIVE":
        raise AppException("Phòng ban không hoạt động", code="DEPARTMENT_INVALID", status_code=422)
    if dataset.owner_department_id != expected:
        raise AppException(
            "Phòng sở hữu đã thay đổi. Vui lòng tải lại",
            code="DATASET_OWNER_CONFLICT",
            status_code=409,
        )
    dataset.owner_department_id = owner
    await db.flush()
    return dataset
