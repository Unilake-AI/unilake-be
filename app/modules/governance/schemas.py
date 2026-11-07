"""Các schema Pydantic cho endpoint quản trị dữ liệu."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

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

class LineageNode(BaseModel):
    id: str
    label: str
    layer: str  # SOURCE, BRONZE, SILVER
    details: dict[str, Any] = Field(default_factory=dict)
    
class LineageEdge(BaseModel):
    source: str
    target: str
    lineage_type: str  # INGESTION, CLEANING_AND_TRANSFORMATION
    job_id: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    
class LineageGraphResponse(BaseModel):
    nodes: list[LineageNode]
    edges: list[LineageEdge]
