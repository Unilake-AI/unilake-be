"""Các mô hình SQLAlchemy cho mô-đun bảng điều khiển."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base

__all__ = ["ChartWidget", "Dashboard", "Kpi", "KpiValue"]


class Dashboard(Base):
    __tablename__ = "dashboard"
    __table_args__ = (
        Index("ix_dashboard_owner_app_user_id", "owner_app_user_id"),
        {"schema": "app"},
    )

    dashboard_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    dashboard_name: Mapped[str] = mapped_column(String(150), nullable=False)
    owner_app_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.app_user.app_user_id"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Kpi(Base):
    __tablename__ = "kpi"
    __table_args__ = (
        Index("ix_kpi_domain", "domain"),
        {"schema": "app"},
    )

    kpi_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    kpi_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    kpi_name: Mapped[str] = mapped_column(String(150), nullable=False)
    domain: Mapped[str] = mapped_column(String(50), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class KpiValue(Base):
    __tablename__ = "kpi_value"
    __table_args__ = (
        Index("idx_kpi_value_period", "kpi_id", "period_date"),
        {"schema": "app"},
    )

    kpi_value_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    kpi_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("app.kpi.kpi_id"), nullable=False)
    period_date: Mapped[date] = mapped_column(Date, nullable=False)
    kpi_value: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    dimension_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class ChartWidget(Base):
    __tablename__ = "chart_widget"
    __table_args__ = (
        Index("ix_chart_widget_dashboard_id", "dashboard_id"),
        Index("ix_chart_widget_query_request_id", "query_request_id"),
        Index("ix_chart_widget_kpi_id", "kpi_id"),
        {"schema": "app"},
    )

    chart_widget_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    dashboard_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.dashboard.dashboard_id"), nullable=False
    )
    widget_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    query_request_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.query_request.query_request_id"), nullable=True
    )
    kpi_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.kpi.kpi_id"), nullable=True
    )
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    position_x: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position_y: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
