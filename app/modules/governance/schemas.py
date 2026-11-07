"""Các schema Pydantic cho endpoint quản trị dữ liệu."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.domains.base import DataLayer  # noqa: TC001


class FieldDefinitionSchema(BaseModel):
    """Schema metadata trường cho phản hồi quản trị dữ liệu."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=150)
    data_type: str = Field(min_length=1, max_length=50)
    nullable: bool = True
    description: str = ""


class CatalogEntrySchema(BaseModel):
    """Schema mục catalog cho phản hồi quản trị dữ liệu."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    domain_id: str = Field(min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    dataset: str = Field(min_length=1, max_length=150)
    layer: DataLayer
    schema_version: str = Field(min_length=1, max_length=30)
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
