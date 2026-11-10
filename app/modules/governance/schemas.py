"""Các schema Pydantic cho endpoint quản trị dữ liệu."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.base import DataLayer  # noqa: TC001


class FieldDefinitionSchema(BaseModel):
    """Schema metadata trường cho phản hồi quản trị dữ liệu."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=150)
    data_type: str = Field(min_length=1, max_length=50)
    nullable: bool = True
    description: str = ""


class CatalogTableSchema(BaseModel):
    """Table thuộc dataset và metadata các cột của table đó."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=150)
    description: str = ""
    columns: list[FieldDefinitionSchema]

    @model_validator(mode="after")
    def unique_column_names(self) -> Self:
        names = [column.name for column in self.columns]
        if len(names) != len(set(names)):
            raise ValueError("Tên cột trong cùng table không được trùng nhau.")
        return self


class CatalogEntrySchema(BaseModel):
    """Schema mục catalog cho phản hồi quản trị dữ liệu."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    domain_id: str = Field(min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    dataset: str = Field(min_length=1, max_length=150)
    layer: DataLayer
    schema_version: str = Field(min_length=1, max_length=30)
    description: str
    fields: list[FieldDefinitionSchema] = Field(default_factory=list)
    tables: list[CatalogTableSchema] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_field_names(self) -> Self:
        # Không cho phép mô tả hai cột cùng tên trong một dataset.
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("Tên cột bị trùng. Vui lòng đặt tên khác nhau cho từng cột.")
        if "tables" not in self.model_fields_set:
            self.tables = [
                CatalogTableSchema(
                    name=self.dataset, description=self.description, columns=self.fields
                )
            ]
        table_names = [table.name for table in self.tables]
        if len(table_names) != len(set(table_names)):
            raise ValueError("Tên table trong cùng dataset không được trùng nhau.")
        legacy_fields = self.tables[0].columns if len(self.tables) == 1 else []
        if "fields" in self.model_fields_set and self.fields != legacy_fields:
            raise ValueError(
                "fields phải khớp columns của table duy nhất; nhiều table dùng fields: []."
            )
        self.fields = legacy_fields
        return self


class CatalogUpdateSchema(BaseModel):
    """PATCH chỉ đổi metadata; domain_id/dataset là khóa cố định trên URL."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    layer: DataLayer | None = None
    schema_version: str | None = Field(default=None, min_length=1, max_length=30)
    description: str | None = None
    fields: list[FieldDefinitionSchema] | None = None
    tables: list[CatalogTableSchema] | None = None

    @model_validator(mode="after")
    def reject_explicit_null(self) -> Self:
        # Bỏ qua field nghĩa là giữ nguyên; gửi null không có nghĩa là xóa field đó.
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError(
                "Thông tin gửi lên chưa hợp lệ. Vui lòng kiểm tra các mục cần sửa; "
                "bỏ qua mục muốn giữ nguyên. Để xóa mô tả hoặc danh sách cột, "
                "hãy gửi mô tả rỗng hoặc danh sách cột rỗng."
            )
        if self.fields is not None:
            names = [field.name for field in self.fields]
            if len(names) != len(set(names)):
                raise ValueError("Tên cột bị trùng. Vui lòng đặt tên khác nhau cho từng cột.")
        if self.tables is not None:
            names = [table.name for table in self.tables]
            if len(names) != len(set(names)):
                raise ValueError("Tên table trong cùng dataset không được trùng nhau.")
            legacy_fields = self.tables[0].columns if len(self.tables) == 1 else []
            if self.fields is not None and self.fields != legacy_fields:
                raise ValueError("fields không khớp với tables.")
        return self


class LineageNode(BaseModel):
    """Nút trên đồ thị phả hệ dữ liệu cho Frontend (ReactFlow / D3.js)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    layer: str
    label: str
    data: dict[str, object] = Field(default_factory=dict)


class LineageEdge(BaseModel):
    """Cạnh nối trên đồ thị phả hệ dữ liệu thể hiện dòng chảy và chuyển dịch dữ liệu."""

    model_config = ConfigDict(extra="ignore")

    id: str
    source: str
    target: str
    source_layer: str
    target_layer: str
    label: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    data: dict[str, object] = Field(default_factory=dict)
    created_at: str | None = None


class LineageGraphResponse(BaseModel):
    """Phản hồi đồ thị Data Lineage chuẩn Nodes & Edges."""

    model_config = ConfigDict(extra="ignore")

    nodes: list[LineageNode]
    edges: list[LineageEdge]
