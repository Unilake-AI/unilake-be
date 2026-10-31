"""Tạo profile cấu trúc từ hợp đồng S1, không nạp các tệp toàn cục của repo."""

from __future__ import annotations

from collections.abc import Mapping

from app.modules.pipeline.schema_mapping import (
    FieldMapping,
    MappingKey,
    MappingProfile,
    validate_profile,
)


def pointer(name: str) -> str:
    return "/" + name.replace("~", "~0").replace("/", "~1")


def build_profile(
    *,
    key: MappingKey,
    source_type: str,
    source_columns: tuple[str, ...],
    target_fields: tuple[str, ...],
    field_paths: Mapping[str, tuple[str, ...]],
    deferred_fields: tuple[str, ...] = (),
    ignored_sources: tuple[str, ...] = (),
) -> MappingProfile:
    """Đường dẫn tường minh cho phép nhiều bố cục của cùng giao thức truyền tải và thực thể.

    Không suy đoán chuyển đổi hoa/thường hay bí danh; bên gọi cung cấp trường hợp đồng nguồn và đích.
    Giá trị cho trường đích hoãn xử lý phải do S3/S4/kiểm toán tạo ra, không dùng giá trị mặc định.
    """
    profile = MappingProfile(
        key=key,
        source_type=source_type,
        target_fields=target_fields,
        fields=tuple(
            FieldMapping(target, paths, "copy" if len(paths) == 1 else "coalesce")
            for target, paths in field_paths.items()
        ),
        source_fields=source_columns,
        deferred_fields=deferred_fields,
        ignored_sources=ignored_sources,
    )
    validate_profile(profile)
    return profile
