"""API metadata catalog: xác thực → DATA_ADMIN → validate → service → DB."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import get_db
from app.modules.governance.schemas import (
    CatalogEntrySchema,
    FieldDefinitionSchema,
    LineageGraphResponse,
)
from app.modules.governance.service import GovernanceService
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.core.responses import COMMON_ERROR_RESPONSES, ErrorResponse
from app.core.roles import RoleCode
from app.dependencies import get_db
from app.modules.governance.catalog_service import CatalogService
from app.modules.governance.schemas import CatalogEntrySchema, CatalogUpdateSchema

router = APIRouter(
    prefix="/governance",
    tags=["governance"],
    # Áp dụng tại router để tất cả endpoint đều yêu cầu đúng role DATA_ADMIN.
    dependencies=[Depends(require_role(RoleCode.DATA_ADMIN))],
    responses={
        **COMMON_ERROR_RESPONSES,
        409: {"model": ErrorResponse, "description": "Bộ dữ liệu đã có trong danh mục."},
    },
)


def get_catalog_service(db: Annotated[AsyncSession, Depends(get_db)]) -> CatalogService:
    # get_db quản lý transaction: thành công commit, có exception rollback.
    return CatalogService(db)


Catalog = Annotated[CatalogService, Depends(get_catalog_service)]


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

@router.get("/lineage", response_model=LineageGraphResponse, summary="Tra cứu Data Lineage đồ thị")
async def get_data_lineage(
    target_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> LineageGraphResponse:
    """Trả về cấu trúc đồ thị nodes và edges theo dõi hành trình dữ liệu."""
    service = GovernanceService()
    return await service.get_lineage_graph(db, target_id=target_id)
