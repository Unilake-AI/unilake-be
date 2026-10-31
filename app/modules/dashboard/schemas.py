"""Các schema Pydantic cho API bảng điều khiển."""

from __future__ import annotations

from pydantic import BaseModel


class KpiRead(BaseModel):
    """Dữ liệu KPI của bảng điều khiển."""

    key: str
    value: float
    unit: str | None = None
