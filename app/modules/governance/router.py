"""Các tuyến API quản trị dữ liệu."""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.governance.schemas import CatalogEntrySchema, FieldDefinitionSchema
from app.modules.governance.service import GovernanceService

router = APIRouter(prefix="/governance", tags=["governance"])


@router.get("/catalog", response_model=list[CatalogEntrySchema])
def get_catalog(domain_id: str | None = None) -> list[CatalogEntrySchema]:
    """Trả về các mục catalog được tạo từ metadata miền nghiệp vụ đã đăng ký."""
    entries = GovernanceService().catalog(domain_id=domain_id)
    return [
        CatalogEntrySchema(
            domain_id=entry.domain_id,
            dataset=entry.dataset,
            layer=entry.layer,
            schema_version=entry.schema_version,
            description=entry.description,
            fields=[
                FieldDefinitionSchema(
                    name=f.name,
                    data_type=f.data_type,
                    nullable=f.nullable,
                    description=f.description,
                )
                for f in entry.fields
            ],
        )
        for entry in entries
    ]
