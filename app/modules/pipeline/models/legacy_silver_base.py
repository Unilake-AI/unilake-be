"""Mô hình SQL v1 chỉ cho adapter SQLite kiểm thử và đọc migration cũ.

Không thuộc Base.metadata của ứng dụng nên create_all/autogenerate không thể
khôi phục các bảng Silver đã chuyển sang MinIO.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class LegacySilverBase(DeclarativeBase):
    pass
