"""Các schema Pydantic cho mô-đun người dùng."""

from __future__ import annotations

from pydantic import BaseModel


class UserRead(BaseModel):
    """Dữ liệu người dùng công khai."""

    id: int
    email: str
    roles: list[str] = []
