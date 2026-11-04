"""CRUD metadata hiển thị trong catalog, không thay đổi hợp đồng pipeline/NLQ."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import AppException
from app.domains.registry import get_domain_registry
from app.modules.governance.repository import CatalogRepository
from app.modules.governance.schemas import CatalogEntrySchema, CatalogUpdateSchema
from app.modules.governance.service import GovernanceService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.domains.registry import DomainRegistry


class CatalogService:
    def __init__(self, db: AsyncSession, registry: DomainRegistry | None = None) -> None:
        self.registry = registry or get_domain_registry()
        self.repo = CatalogRepository(db)

    def _ensure_domain(self, domain_id: str) -> None:
        # Chỉ đăng ký metadata vào lĩnh vực đã tồn tại trong hệ thống.
        try:
            self.registry.get(domain_id)
        except LookupError as exc:
            raise AppException(
                "Lĩnh vực bạn chọn không tồn tại. Vui lòng chọn lại lĩnh vực.",
                code="DOMAIN_NOT_FOUND",
                status_code=404,
            ) from exc

    async def list(self, domain_id: str | None = None) -> list[CatalogEntrySchema]:
        if domain_id is not None:
            self._ensure_domain(domain_id)

        # 1. Khai báo trong code là dữ liệu nền, không ghi DB mỗi lần đọc.
        entries = {
            (entry.domain_id, entry.dataset): CatalogEntrySchema.model_validate(
                entry, from_attributes=True
            )
            for entry in GovernanceService(self.registry).catalog(domain_id=domain_id)
        }
        # 2. Metadata trong DB được ưu tiên; dấu xóa ẩn cả bản khai báo sẵn.
        for row in await self.repo.list(domain_id):
            key = (row.domain_id, row.dataset)
            if row.is_deleted:
                entries.pop(key, None)
            else:
                entries[key] = CatalogEntrySchema.model_validate(row.metadata_payload)
        return [entries[key] for key in sorted(entries)]

    async def get(self, domain_id: str, dataset: str) -> CatalogEntrySchema:
        for entry in await self.list(domain_id):
            if entry.dataset == dataset:
                return entry
        raise AppException(
            "Không tìm thấy bộ dữ liệu trong danh mục. Vui lòng tải lại danh sách và thử lại.",
            code="CATALOG_NOT_FOUND",
            status_code=404,
        )

    async def create(self, request: CatalogEntrySchema) -> CatalogEntrySchema:
        self._ensure_domain(request.domain_id)
        entries = await self.list(request.domain_id)
        if any(entry.dataset == request.dataset for entry in entries):
            self._duplicate()
        # Unique key trong DB cũng chặn trùng khi nhiều request đến cùng lúc.
        if not await self.repo.save(request, create=True):
            self._duplicate()
        return request

    async def update(
        self, domain_id: str, dataset: str, request: CatalogUpdateSchema
    ) -> CatalogEntrySchema:
        current = await self.get(domain_id, dataset)
        # PATCH chỉ thay field được gửi. Nếu gửi fields, thay toàn bộ danh sách cột.
        changes = request.model_dump(exclude_unset=True, mode="json")
        updated = CatalogEntrySchema.model_validate(current.model_dump(mode="json") | changes)
        if changes:
            await self.repo.save(updated)
        return updated

    async def delete(self, domain_id: str, dataset: str) -> None:
        current = await self.get(domain_id, dataset)
        # Xóa metadata bằng tombstone; không DROP TABLE hay xóa dữ liệu vật lý.
        await self.repo.save(current, deleted=True)

    @staticmethod
    def _duplicate() -> None:
        raise AppException(
            "Tên bộ dữ liệu đã có trong danh mục của lĩnh vực này. Vui lòng chọn tên khác.",
            code="CATALOG_ALREADY_EXISTS",
            status_code=409,
        )
