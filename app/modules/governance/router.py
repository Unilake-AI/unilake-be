"""API metadata catalog: xác thực → DATA_ADMIN → validate → service → DB."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_role
from app.core.responses import COMMON_ERROR_RESPONSES, ErrorResponse
from app.core.roles import RoleCode
from app.dependencies import get_db
from app.modules.governance.catalog_service import CatalogService
from app.modules.governance.repository import LineageRepository, build_lineage_graph
from app.modules.governance.schemas import (
    CatalogEntrySchema,
    CatalogUpdateSchema,
    LineageGraphResponse,
)

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
async def get_catalog(service: Catalog, domain_id: str | None = None) -> list[CatalogEntrySchema]:
    """Đọc catalog hiệu lực, có thể lọc theo domain_id; chỉ DATA_ADMIN."""
    return await service.list(domain_id)


@router.get("/catalog/{domain_id}/{dataset}", response_model=CatalogEntrySchema)
async def get_catalog_detail(domain_id: str, dataset: str, service: Catalog) -> CatalogEntrySchema:
    """Xem chi tiết metadata của một dataset theo lĩnh vực và tên."""
    return await service.get(domain_id, dataset)


@router.post("/catalog", response_model=CatalogEntrySchema, status_code=201)
async def create_catalog(request: CatalogEntrySchema, service: Catalog) -> CatalogEntrySchema:
    """Thêm metadata dataset vào lĩnh vực đã đăng ký; không tạo bảng vật lý."""
    return await service.create(request)


@router.patch("/catalog/{domain_id}/{dataset}", response_model=CatalogEntrySchema)
async def update_catalog(
    domain_id: str, dataset: str, request: CatalogUpdateSchema, service: Catalog
) -> CatalogEntrySchema:
    """Sửa một phần metadata; fields nếu gửi sẽ thay toàn bộ danh sách cột."""
    return await service.update(domain_id, dataset, request)


@router.delete("/catalog/{domain_id}/{dataset}", status_code=204)
async def delete_catalog(domain_id: str, dataset: str, service: Catalog) -> Response:
    """Ẩn metadata khỏi catalog; giữ nguyên dữ liệu vật lý và schema pipeline."""
    await service.delete(domain_id, dataset)
    return Response(status_code=204)


@router.get("/lineage", response_model=LineageGraphResponse)
async def get_lineage(
    db: Annotated[AsyncSession, Depends(get_db)],
    domain_id: str | None = None,
    layer: str | None = None,
    search: str | None = None,
) -> LineageGraphResponse:
    """Tra cứu cây phả hệ dữ liệu (Data Lineage) chuẩn đồ thị Nodes & Edges cho ReactFlow / D3.js."""
    repo = LineageRepository(db)
    rows = await repo.list(domain_id=domain_id, layer=layer, search=search)
    return build_lineage_graph(rows)
