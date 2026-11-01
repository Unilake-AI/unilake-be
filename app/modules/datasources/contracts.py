"""Cấu hình nguồn công khai được chiếu từ catalog, độc lập với ORM."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CatalogSource:
    is_active: bool
    source_type: str
    location: str | None
    configuration: dict[str, Any]
