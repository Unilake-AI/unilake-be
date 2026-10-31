"""Lớp cơ sở khai báo dùng chung cho các bộ điều hợp lưu trữ UniLake."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Lớp cơ sở cho các mô hình lưu trữ SQLAlchemy."""
