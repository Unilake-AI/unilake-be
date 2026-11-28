"""Các schema Pydantic cho mô-đun người dùng."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.core.roles import RoleCode  # noqa: TC001 - Pydantic resolves this at runtime


class UserRead(BaseModel):
    """Dữ liệu người dùng công khai."""

    id: int
    email: str
    roles: list[str] = []


class EmployeeLink(BaseModel):
    """Liên kết tài khoản với hồ sơ nhân viên có thật; kiểm tra bản cũ để tránh ghi đè."""

    model_config = ConfigDict(extra="forbid")

    employee_id: int = Field(gt=0)
    expected_employee_id: int | None


class AssignmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: int = Field(gt=0)
    department_id: int = Field(gt=0)
    position_id: int = Field(gt=0)
    start_date: date
    end_date: date | None = None
    is_primary: bool = False

    @model_validator(mode="after")
    def valid_dates(self) -> AssignmentCreate:
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("Ngày kết thúc phải từ ngày bắt đầu trở đi")
        return self


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.@-]+$")
    password: SecretStr
    roles: set[RoleCode] = Field(min_length=1)


class AccountAccessUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roles: set[RoleCode] = Field(min_length=1)
    is_active: bool
    expected_roles: set[str]
    expected_is_active: bool


class IAMAuditEvent(BaseModel):
    event_id: int
    actor_id: int
    username: str
    target_id: int | None
    action: str
    occurred_at: datetime
    outcome: Literal["SUCCESS", "REJECTED"]
    changes: dict[str, Any] | None
    error_code: str | None
