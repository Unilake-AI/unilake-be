"""Truy vấn metadata; transaction được commit/rollback bởi dependency get_db."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.modules.governance.models import CatalogMetadata, PipelineLineage
from app.modules.governance.schemas import LineageEdge, LineageGraphResponse, LineageNode

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.governance.schemas import CatalogEntrySchema


class CatalogRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(self, domain_id: str | None = None) -> list[CatalogMetadata]:
        stmt = select(CatalogMetadata)
        if domain_id is not None:
            stmt = stmt.where(CatalogMetadata.domain_id == domain_id)
        return list((await self.db.scalars(stmt)).all())

    async def save(
        self, entry: CatalogEntrySchema, *, deleted: bool = False, create: bool = False
    ) -> bool:
        # UPSERT lưu cả bản sửa của khai báo tĩnh lẫn dataset do API tạo.
        # json mode chuyển enum layer và danh sách cột thành JSON thuần.
        stmt = insert(CatalogMetadata).values(
            domain_id=entry.domain_id,
            dataset=entry.dataset,
            metadata_payload=entry.model_dump(mode="json"),
            is_deleted=deleted,
        )
        upsert = stmt.on_conflict_do_update(
            index_elements=[CatalogMetadata.domain_id, CatalogMetadata.dataset],
            set_={
                "metadata_payload": stmt.excluded.metadata_payload,
                "is_deleted": deleted,
                "updated_at": func.now(),
            },
            # POST chỉ khôi phục bản đã xóa; hai POST đồng thời không ghi đè nhau.
            where=CatalogMetadata.is_deleted.is_(True) if create else None,
        ).returning(CatalogMetadata.dataset)
        return (await self.db.execute(upsert)).scalar_one_or_none() is not None


class LineageRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(
        self,
        *,
        domain_id: str | None = None,
        layer: str | None = None,
        search: str | None = None,
    ) -> list[PipelineLineage]:
        stmt = select(PipelineLineage).order_by(
            PipelineLineage.created_at.asc(), PipelineLineage.lineage_id.asc()
        )
        if layer:
            layer_upper = layer.upper()
            stmt = stmt.where(
                or_(
                    PipelineLineage.source_layer == layer_upper,
                    PipelineLineage.target_layer == layer_upper,
                )
            )
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    PipelineLineage.source_name.ilike(pattern),
                    PipelineLineage.target_name.ilike(pattern),
                )
            )
        rows = list((await self.db.scalars(stmt)).all())
        if domain_id:
            rows = [
                row
                for row in rows
                if isinstance(row.lineage_metadata, dict)
                and row.lineage_metadata.get("domain") == domain_id
            ]
        return rows

    async def save(
        self,
        *,
        source_layer: str,
        source_name: str,
        target_layer: str,
        target_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> PipelineLineage:
        entry = PipelineLineage(
            source_layer=source_layer,
            source_name=source_name,
            target_layer=target_layer,
            target_name=target_name,
            lineage_metadata=metadata or {},
        )
        self.db.add(entry)
        await self.db.flush()
        return entry


def build_lineage_graph(rows: list[PipelineLineage]) -> LineageGraphResponse:
    nodes_map: dict[str, LineageNode] = {}
    edges: list[LineageEdge] = []

    for row in rows:
        source_id = f"{row.source_layer}:{row.source_name}"
        target_id = f"{row.target_layer}:{row.target_name}"

        source_label = row.source_name.split("/")[-1] if "/" in row.source_name else row.source_name
        target_label = row.target_name.split("/")[-1] if "/" in row.target_name else row.target_name

        if source_id not in nodes_map:
            nodes_map[source_id] = LineageNode(
                id=source_id,
                name=row.source_name,
                layer=row.source_layer,
                label=source_label,
                data={
                    "label": source_label,
                    "name": row.source_name,
                    "layer": row.source_layer,
                },
            )

        if target_id not in nodes_map:
            nodes_map[target_id] = LineageNode(
                id=target_id,
                name=row.target_name,
                layer=row.target_layer,
                label=target_label,
                data={
                    "label": target_label,
                    "name": row.target_name,
                    "layer": row.target_layer,
                },
            )

        edge_id = f"lineage-{row.lineage_id}"
        meta = row.lineage_metadata or {}
        edge_label = f"{row.source_layer} → {row.target_layer}"
        edges.append(
            LineageEdge(
                id=edge_id,
                source=source_id,
                target=target_id,
                source_layer=row.source_layer,
                target_layer=row.target_layer,
                label=edge_label,
                metadata=meta,
                data={**meta, "label": edge_label},
                created_at=row.created_at.isoformat() if row.created_at else None,
            )
        )

    return LineageGraphResponse(nodes=list(nodes_map.values()), edges=edges)
