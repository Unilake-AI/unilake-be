"""Các schema Pydantic cho endpoint quản trị dữ liệu."""

from __future__ import annotations

from pydantic import BaseModel

from app.domains.base import DataLayer  # noqa: TC001


class FieldDefinitionSchema(BaseModel):
    """Schema metadata trường cho phản hồi quản trị dữ liệu."""

    name: str
    data_type: str
    nullable: bool = True
    description: str = ""


class CatalogEntrySchema(BaseModel):
    """Schema mục catalog cho phản hồi quản trị dữ liệu."""

    domain_id: str
    dataset: str
    layer: DataLayer
    schema_version: str
    description: str
    fields: list[FieldDefinitionSchema]
