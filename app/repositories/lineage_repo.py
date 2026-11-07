"""Repository quản lý và truy vấn Data Lineage trên PostgreSQL."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class LineageRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def record_edge(
        self,
        *,
        source_layer: str,
        source_id: str,
        source_name: str,
        target_layer: str,
        target_id: str,
        target_name: str,
        job_id: int | None = None,
        lineage_type: str = "DIRECT_COPY",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Ghi nhận một cạnh (Edge) quan hệ giữa nguồn và đích vào DB."""
        # Lưu vào bảng ingestion_job hoặc bảng liên kết pipeline_lineage
        query = text("""
            INSERT INTO app.pipeline_lineage (
                source_layer, source_id, source_name,
                target_layer, target_id, target_name,
                job_id, lineage_type, metadata, created_at
            ) VALUES (
                :source_layer, :source_id, :source_name,
                :target_layer, :target_id, :target_name,
                :job_id, :lineage_type, :metadata, NOW()
            )
        """)
        await self.db.execute(
            query,
            {
                "source_layer": source_layer,
                "source_id": source_id,
                "source_name": source_name,
                "target_layer": target_layer,
                "target_id": target_id,
                "target_name": target_name,
                "job_id": job_id,
                "lineage_type": lineage_type,
                "metadata": json.dumps(metadata or {}),
            },
        )

    async def get_lineage_graph(self, target_id: str | None = None) -> list[dict[str, Any]]:
        """Truy vấn toàn bộ các cạnh quan hệ Lineage."""
        sql = """
            SELECT source_layer, source_id, source_name,
                   target_layer, target_id, target_name,
                   job_id, lineage_type, metadata, created_at
            FROM app.pipeline_lineage
        """
        params = {}
        if target_id:
            sql += " WHERE target_id = :target_id OR source_id = :target_id"
            params["target_id"] = target_id
            
        result = await self.db.execute(text(sql), params)
        return [dict(row) for row in result.mappings().all()]