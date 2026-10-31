"""Các schema Pydantic cho API nguồn dữ liệu."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class SourceSystemRead(BaseModel):
    """Payload trả về thông tin hệ thống nguồn nghiệp vụ."""

    model_config = ConfigDict(from_attributes=True)

    source_system_id: int
    source_code: str
    source_name: str
    source_type: str
    description: str | None = None
    owner_department_id: int | None = None
    is_active: bool
    created_at: datetime


class DataSourceRead(BaseModel):
    """Payload rút gọn, giữ tương thích với API/test cũ."""

    id: int
    name: str
    source_type: str


class DataSourceFilter(BaseModel):
    """Bộ lọc và phân trang khi lấy danh sách nguồn dữ liệu."""

    # Các field filter đều optional để caller có thể list toàn bộ data source.
    source_system_id: int | None = None
    source_type: str | None = None
    is_active: bool | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class DataSourceRequest(DataSourceFilter):
    """Alias request cũ, tái sử dụng DataSourceFilter để không vỡ caller hiện tại."""


class DataSourceItem(BaseModel):
    """Một dòng data source chi tiết dùng cho API list/detail."""

    model_config = ConfigDict(from_attributes=True)

    data_source_id: int
    source_system_id: int
    # Tên source system thường lấy từ join, nên không bắt buộc có trên ORM DataSource.
    source_system_name: str | None = None
    source_name: str
    source_type: str
    connection_type: str | None = None
    location: str | None = None
    # Không trả configuration/credential trong response list để tránh lộ thông tin kết nối.
    description: str | None = None
    is_active: bool
    last_ingested_at: datetime | None = None
    last_ingestion_status: str | None = None
    ingestion_count: int
    created_by: int | None = None
    created_at: datetime
    updated_at: datetime | None = None


class DataSourceBase(DataSourceItem):
    """Alias item cũ, giữ tên DataSourceBase cho code đã dùng trước đó."""


class DataSourceResponse(BaseModel):
    """Response phân trang cho danh sách nguồn dữ liệu."""

    model_config = ConfigDict(populate_by_name=True)

    # Nhận cả "item" từ schema cũ và "items" theo naming mới.
    items: list[DataSourceItem] = Field(validation_alias=AliasChoices("items", "item"))
    total: int
    page: int
    page_size: int


class DataLayerRead(BaseModel):
    """Payload trả về thông tin tầng dữ liệu trong lakehouse."""

    model_config = ConfigDict(from_attributes=True)

    data_layer_id: int
    layer_code: str
    layer_name: str
    storage_type: str | None = None
    description: str | None = None


class DatasetRead(BaseModel):
    """Payload trả về metadata của dataset đã đăng ký."""

    model_config = ConfigDict(from_attributes=True)

    dataset_id: int
    dataset_code: str
    dataset_name: str
    domain: str
    source_system_id: int | None = None
    data_layer_id: int
    owner_department_id: int | None = None
    storage_format: str | None = None
    location: str | None = None
    version: str | None = None
    status: str
    description: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class DataSourceCreate(BaseModel):
    """Payload tạo mới data source; configuration được validate theo source_type."""

    source_system_id: int
    source_name: str
    source_type: str
    connection_type: str
    location: str
    configuration: dict[str, Any]
    description: str | None = None
    is_active: bool


class DataSourceUpdate(DataSourceCreate):
    """Payload cập nhật data source; tất cả field đều optional để hỗ trợ partial update."""

    source_system_id: int | None = None  # type: ignore[assignment]
    source_name: str | None = None  # type: ignore[assignment]
    source_type: str | None = None  # type: ignore[assignment]
    connection_type: str | None = None  # type: ignore[assignment]
    location: str | None = None  # type: ignore[assignment]
    configuration: dict[str, Any] | None = None  # type: ignore[assignment]
    description: str | None = None
    is_active: bool | None = None  # type: ignore[assignment]
