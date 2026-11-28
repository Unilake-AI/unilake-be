"""Truy vấn thống kê Gold và catalog theo điều kiện truy cập của bên gọi."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.modules.governance.contracts import CatalogTableSchema, FieldDefinitionSchema
from app.shared.reporting import GoldAnalyticsFilters, GoldMetric, MetricPage, ReportFilters

DatasetScope = tuple[str, dict[str, Any]]


class ExplorerDataset(BaseModel):
    dataset_id: int
    dataset_code: str
    dataset_name: str
    domain: str
    layer: str
    description: str | None
    updated_at: datetime | None
    last_published_at: datetime | None
    tables: list[CatalogTableSchema] = Field(default_factory=list)


class ExplorerPage(BaseModel):
    items: list[ExplorerDataset]
    total: int
    limit: int
    offset: int


async def metrics(
    db: AsyncSession, scope: DatasetScope, filters: GoldAnalyticsFilters
) -> MetricPage:
    clause, params = scope
    params = params.copy()
    if filters.dimension_value is not None and not filters.metric.startswith("record_count_by_"):
        raise AppException(
            "Bộ lọc nhóm cần phép thống kê theo chiều tương ứng",
            code="INVALID_ANALYTICS_FILTER",
            status_code=422,
        )
    params.update(
        entity=filters.entity,
        metric=filters.metric,
        limit=filters.limit,
        offset=filters.offset,
        dimension=filters.metric.removeprefix("record_count_by_"),
        dimension_value=filters.dimension_value,
    )
    query = (
        " FROM gold.current_metrics m JOIN app.dataset d ON d.dataset_id=m.dataset_id AND d.domain=m.domain WHERE "
        + clause
        + " AND (CAST(:entity AS text) IS NULL OR m.entity=:entity) AND m.metric=:metric"
        " AND (CAST(:dimension_value AS text) IS NULL OR m.dimensions->>:dimension=:dimension_value)"
    )
    total = (await db.execute(text("SELECT count(*)" + query), params)).scalar_one()
    rows = (
        await db.execute(
            text(
                "SELECT m.*,d.dataset_name"
                + query
                + " ORDER BY m.scope,m.entity,m.metric,m.dimensions LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings()
    return MetricPage(
        items=[GoldMetric.model_validate(row) for row in rows],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


async def catalog(db: AsyncSession, scope: DatasetScope, filters: ReportFilters) -> ExplorerPage:
    clause, params = scope
    total = int(
        (
            await db.execute(text("SELECT count(*) FROM app.dataset d WHERE " + clause), params)
        ).scalar_one()
    )
    rows = (
        (
            await db.execute(
                text(
                    "SELECT d.dataset_id,d.dataset_code,d.dataset_name,d.domain,d.description,"
                    " coalesce(d.updated_at,d.created_at) AS updated_at,dl.layer_code AS layer,"
                    " (SELECT max(m.finished_at) FROM gold.current_metrics m WHERE m.dataset_id=d.dataset_id AND m.domain=d.domain) AS last_published_at"
                    " FROM app.dataset d JOIN app.data_layer dl ON dl.data_layer_id=d.data_layer_id WHERE "
                    + clause
                    + " ORDER BY d.dataset_id LIMIT :limit OFFSET :offset"
                ),
                {**params, "limit": filters.limit, "offset": filters.offset},
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        return ExplorerPage(items=[], total=total, limit=filters.limit, offset=filters.offset)
    columns = (
        (
            await db.execute(
                text(
                    "SELECT t.dataset_id,t.table_name,t.description AS table_description,c.column_name,c.data_type,c.is_nullable,c.description"
                    " FROM app.catalog_table t LEFT JOIN app.catalog_column c ON c.catalog_table_id=t.catalog_table_id"
                    " WHERE t.dataset_id=ANY(:ids) ORDER BY t.catalog_table_id,c.catalog_column_id"
                ),
                {"ids": [row["dataset_id"] for row in rows]},
            )
        )
        .mappings()
        .all()
    )
    tables: dict[int, dict[str, CatalogTableSchema]] = {}
    for column in columns:
        table = tables.setdefault(column["dataset_id"], {}).setdefault(
            column["table_name"],
            CatalogTableSchema(
                name=column["table_name"], description=column["table_description"] or "", columns=[]
            ),
        )
        if column["column_name"]:
            table.columns.append(
                FieldDefinitionSchema(
                    name=column["column_name"],
                    data_type=column["data_type"],
                    nullable=column["is_nullable"] is not False,
                    description=column["description"] or "",
                )
            )
    return ExplorerPage(
        items=[
            ExplorerDataset(**row, tables=list(tables.get(row["dataset_id"], {}).values()))
            for row in rows
        ],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )
