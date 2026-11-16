"""Các schema Pydantic cho mô-đun người dùng."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
