"""Các mô hình SQLAlchemy cho mô-đun xác thực."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base

__all__ = ["Base", "LoginSession", "Permission", "Role", "RolePermission", "UserRole"]


class Role(Base):
    __tablename__ = "role"
    __table_args__ = (Index("ix_role_role_code", "role_code"), {"schema": "app"})

    role_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    role_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    role_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class Permission(Base):
    __tablename__ = "permission"
    __table_args__ = (
        Index("ix_permission_permission_code", "permission_code"),
        {"schema": "app"},
    )

    permission_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    permission_code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    permission_name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class UserRole(Base):
    __tablename__ = "user_role"
    __table_args__ = (
        Index("ix_user_role_role_id", "role_id"),
        {"schema": "app"},
    )

    app_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.app_user.app_user_id"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.role.role_id"), primary_key=True
    )


class RolePermission(Base):
    __tablename__ = "role_permission"
    __table_args__ = (
        Index("ix_role_permission_permission_id", "permission_id"),
        {"schema": "app"},
    )

    role_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.role.role_id"), primary_key=True
    )
    permission_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.permission.permission_id"), primary_key=True
    )


class LoginSession(Base):
    __tablename__ = "login_session"
    __table_args__ = (
        Index("ix_login_session_app_user_id", "app_user_id"),
        Index("ix_login_session_session_status", "session_status"),
        {"schema": "app"},
    )

    login_session_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    app_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.app_user.app_user_id"), nullable=False
    )
    login_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    logout_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_status: Mapped[str] = mapped_column(String(30), default="ACTIVE", nullable=False)
