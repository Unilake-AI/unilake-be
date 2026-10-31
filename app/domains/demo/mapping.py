"""Các ánh xạ nguồn cho miền demo."""

from __future__ import annotations

from app.domains.base import SourceMapping

MAPPINGS = (
    SourceMapping(
        source_type="demo_csv",
        target_dataset="demo_students",
        version="1.0",
        field_map={
            "id": "student_id",
            "full_name": "name",
            "active": "status",
        },
    ),
)
