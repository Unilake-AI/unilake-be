"""Thông báo lấy từ lịch sử yêu cầu xử lý lỗi, không chứa dữ liệu gốc."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NotificationRead(BaseModel):
    event_id: int
    issue_id: UUID
    title: str
    domain: str
    department_name: str
    event_type: str
    to_status: str
    actor_id: int
    created_at: datetime
    read_at: datetime | None


class NotificationPage(BaseModel):
    items: list[NotificationRead]
    total: int
    unread_count: int
    limit: int
    offset: int


class MarkNotificationsRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_ids: list[int] = Field(min_length=1, max_length=100)
