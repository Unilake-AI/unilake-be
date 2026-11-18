"""Metadata chỉ đọc, giới hạn theo dataset đang hoạt động và phòng sở hữu."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.modules.governance.contracts import CatalogTableSchema, FieldDefinitionSchema
from app.modules.manager.schemas import ReportFilters
from app.repositories.manager_report_repo import dataset_scope

if TYPE_CHECKING:
    from app.modules.manager.access import ManagerAccess


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


class LineageStep(BaseModel):
    id: int
    source_layer: str
    target_layer: str
    source_label: str
    target_label: str
    created_at: datetime
    details: dict[str, Any]


class DatasetLineage(BaseModel):
    dataset_id: int
    steps: list[LineageStep]
    note: str = "Chỉ hiển thị các bước có bằng chứng gắn với bộ dữ liệu này; thiếu bước không được suy diễn là đã chạy."


async def catalog(db: AsyncSession, manager: ManagerAccess, filters: ReportFilters) -> ExplorerPage:
    clause, params = dataset_scope(manager, filters)
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


async def dataset(db: AsyncSession, manager: ManagerAccess, dataset_id: int) -> ExplorerDataset:
    page = await catalog(db, manager, ReportFilters(dataset_id=dataset_id, limit=1))
    if not page.items:
        raise AppException(
            "Không tìm thấy bộ dữ liệu trong phạm vi được xem",
            code="DATASET_NOT_FOUND",
            status_code=404,
        )
    return page.items[0]


async def lineage(db: AsyncSession, manager: ManagerAccess, dataset_id: int) -> DatasetLineage:
    await dataset(db, manager, dataset_id)
    # Dùng ID dataset hoặc job/run đã ghi nhận; không đoán quan hệ theo tên file/domain.
    rows = (
        (
            await db.execute(
                text("""
        SELECT l.lineage_id,l.source_layer,l.target_layer,l.created_at,l.metadata
        FROM app.pipeline_lineage l
        LEFT JOIN app.ingestion_job i ON i.ingestion_job_id::text=l.metadata->>'ingestion_job_id'
        LEFT JOIN app.cleaning_run c ON c.run_id::text=l.metadata->>'cleaning_run_id'
        LEFT JOIN app.gold_job g ON g.job_id::text=l.target_name AND l.target_layer='GOLD'
        WHERE coalesce(l.metadata->>'dataset_id',i.dataset_id::text,c.dataset_id::text,g.dataset_id::text)=:dataset
          AND (l.metadata->>'domain' IS NULL OR l.metadata->>'domain'=(SELECT domain FROM app.dataset WHERE dataset_id=:id))
        ORDER BY l.created_at DESC,l.lineage_id DESC LIMIT 200
    """),
                {"dataset": str(dataset_id), "id": dataset_id},
            )
        )
        .mappings()
        .all()
    )
    allowed = {
        "entity",
        "status",
        "records_read",
        "records_passed",
        "records_rejected",
        "records_quarantined",
        "recipe_version",
    }
    steps = [
        LineageStep(
            id=row["lineage_id"],
            source_layer=row["source_layer"],
            target_layer=row["target_layer"],
            source_label={
                "SOURCE": "Nguồn đã đăng ký",
                "BRONZE": "Dữ liệu gốc",
                "SILVER": "Dữ liệu chuẩn hóa",
                "GOLD": "Báo cáo tổng hợp",
            }.get(row["source_layer"], row["source_layer"]),
            target_label={
                "BRONZE": "Dữ liệu gốc",
                "SILVER": "Dữ liệu chuẩn hóa",
                "GOLD": "Báo cáo tổng hợp",
            }.get(row["target_layer"], row["target_layer"]),
            created_at=row["created_at"],
            details={
                key: value
                for key, value in (row["metadata"] or {}).items()
                if key in allowed and isinstance(value, (str, int, float, bool, type(None)))
            },
        )
        for row in rows
    ]
    return DatasetLineage(dataset_id=dataset_id, steps=steps)
