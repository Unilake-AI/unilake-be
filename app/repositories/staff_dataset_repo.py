"""Đọc các bộ dữ liệu Bronze còn hoạt động để nhân viên lựa chọn."""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.datasources.models import DataLayer, Dataset, SourceSystem


def _available_query(domains: frozenset[str] | None) -> Select[Dataset]:
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
    if domains is not None:
        query = query.where(Dataset.domain.in_(domains))
    return query


async def list_datasets(
    db: AsyncSession,
    domains: frozenset[str] | None,
    *,
    limit: int,
    offset: int,
    search: str | None,
    domain: str | None,
) -> list[Dataset]:
    query = _available_query(domains)
    if domain is not None:
        query = query.where(Dataset.domain == domain)
    if search is not None:
        # Tìm đúng phần tên được nhập, kể cả khi tên chứa dấu phần trăm.
        query = query.where(Dataset.dataset_name.icontains(search.strip(), autoescape=True))
    return list(await db.scalars(query.order_by(Dataset.dataset_id).limit(limit).offset(offset)))


async def get_dataset(
    db: AsyncSession, dataset_id: int, domains: frozenset[str] | None
) -> Dataset | None:
    dataset: Dataset | None = await db.scalar(
        _available_query(domains).where(Dataset.dataset_id == dataset_id)
    )
    return dataset
