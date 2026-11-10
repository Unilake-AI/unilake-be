"""Chọn bộ dữ liệu theo lĩnh vực được cấp quyền, không nhận quyền từ giao diện."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.roles import DATA_WRITE_ROLES, UPLOAD_STAFF_DOMAINS
from app.modules.datasources.contracts import DatasetRead
from app.repositories import staff_dataset_repo


def _domains(user: dict[str, Any]) -> frozenset[str] | None:
    roles = set(user.get("roles", []))
    if not DATA_WRITE_ROLES.isdisjoint(roles):
        return None
    domains = frozenset(domain for role, domain in UPLOAD_STAFF_DOMAINS.items() if role in roles)
    if not domains:
        raise AppException("Không có quyền chọn bộ dữ liệu", code="FORBIDDEN", status_code=403)
    return domains


async def list_datasets(
    db: AsyncSession,
    user: dict[str, Any],
    limit: int = 20,
    offset: int = 0,
    search: str | None = None,
    domain: str | None = None,
) -> list[DatasetRead]:
    rows = await staff_dataset_repo.list_datasets(
        db,
        _domains(user),
        limit=limit,
        offset=offset,
        search=search,
        domain=domain,
    )
    return [DatasetRead.model_validate(row) for row in rows]


async def get_dataset(dataset_id: int, db: AsyncSession, user: dict[str, Any]) -> DatasetRead:
    dataset = await staff_dataset_repo.get_dataset(db, dataset_id, _domains(user))
    if dataset is None:
        # Không tiết lộ bộ dữ liệu của lĩnh vực khác khi có người đoán mã.
        raise AppException("Không tìm thấy bộ dữ liệu", code="DATASET_NOT_FOUND", status_code=404)
    return DatasetRead.model_validate(dataset)
