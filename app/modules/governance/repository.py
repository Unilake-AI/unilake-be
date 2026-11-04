"""Truy vấn metadata; transaction được commit/rollback bởi dependency get_db."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.modules.governance.models import CatalogMetadata

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
