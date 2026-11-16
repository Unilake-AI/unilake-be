"""Chiếu metadata dataset thật vào catalog; không đọc dữ liệu cá nhân trong file."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from app.domains.base import DataLayer as Layer
from app.modules.datasources.models import DataLayer, Dataset
from app.modules.governance.models import CatalogColumn, CatalogTable
from app.modules.governance.schemas import (
    CatalogEntrySchema,
    CatalogTableSchema,
    FieldDefinitionSchema,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def runtime_catalog(db: AsyncSession, domain_id: str | None) -> list[CatalogEntrySchema]:
    stmt = select(Dataset, DataLayer.layer_code).join(DataLayer).where(Dataset.status == "ACTIVE")
    if domain_id is not None:
        stmt = stmt.where(Dataset.domain == domain_id)
    datasets = (await db.execute(stmt.order_by(Dataset.dataset_id))).all()
    if not datasets:
        return []
    tables = list(
        await db.scalars(
            select(CatalogTable)
            .where(CatalogTable.dataset_id.in_([d.dataset_id for d, _ in datasets]))
            .order_by(CatalogTable.catalog_table_id)
        )
    )
    columns = (
        list(
            await db.scalars(
                select(CatalogColumn)
                .where(CatalogColumn.catalog_table_id.in_([t.catalog_table_id for t in tables]))
                .order_by(CatalogColumn.catalog_column_id)
            )
        )
        if tables
        else []
    )
    result = []
    for dataset, layer in datasets:
        if layer.lower() not in {e.value for e in Layer}:
            continue
        table_schemas = [
            CatalogTableSchema(
                name=t.table_name,
                description=t.description or "",
                columns=[
                    FieldDefinitionSchema(
                        name=c.column_name,
                        data_type=c.data_type,
                        nullable=c.is_nullable is not False,
                        description=c.description or "",
                    )
                    for c in columns
                    if c.catalog_table_id == t.catalog_table_id
                ],
            )
            for t in tables
            if t.dataset_id == dataset.dataset_id
        ]
        result.append(
            CatalogEntrySchema(
                domain_id=dataset.domain,
                dataset=dataset.dataset_code,
                layer=Layer(layer.lower()),
                schema_version=dataset.version or "1.0",
                description=dataset.description or dataset.dataset_name,
                tables=table_schemas,
            )
        )
    return result
