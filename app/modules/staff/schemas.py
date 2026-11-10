"""Thông tin nhận và trả về cho các thao tác của nhân viên."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

IssueStatus = Literal["ASSIGNED", "PENDING_VERIFICATION", "VERIFIED"]


class DomainChoice(BaseModel):
    domain: str
    name: str


class DepartmentChoice(BaseModel):
    department_id: int
    department_code: str
    department_name: str


class StaffContext(BaseModel):
    user_id: int
    username: str
    roles: list[str]
    domains: list[DomainChoice]
    default_domain: str | None
    departments: list[DepartmentChoice]
    can_upload: bool
    can_resolve_issues: bool
    can_manage_silver: bool
    needs_department_assignment: bool
    accepted_extensions: list[str]
    max_file_bytes: int
    max_records: int
    note: str


class IssueInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateIssue(IssueInput):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    department_id: int = Field(gt=0)
    assignee_user_id: int | None = Field(default=None, gt=0)
    quarantine_record_ids: list[UUID] = Field(min_length=1, max_length=100)


class AssignIssue(IssueInput):
    assignee_user_id: int = Field(gt=0)


class SubmitResolution(IssueInput):
    ingestion_job_id: int = Field(gt=0)
    note: str = Field(min_length=1, max_length=4000)


class SubmitVerification(IssueInput):
    cleaning_run_id: UUID
    verified: bool
    note: str = Field(min_length=1, max_length=4000)
    confirmed_record_ids: list[UUID] = Field(default_factory=list, max_length=100)


class IssueFilters(BaseModel):
    department_id: int | None = Field(default=None, gt=0)
    assignee_user_id: int | None = Field(default=None, gt=0)
    status: IssueStatus | None = None
    mine_only: bool = False
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class IssueRead(BaseModel):
    issue_id: UUID
    dataset_id: int
    department_id: int
    domain: str
    title: str
    description: str
    status: IssueStatus
    created_by: int
    assignee_user_id: int | None
    resolution_job_id: int | None
    verification_run_id: UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class IssueEventRead(BaseModel):
    event_id: int
    actor_id: int
    event_type: str
    from_status: IssueStatus | None
    to_status: IssueStatus
    details: dict[str, Any]
    created_at: AwareDatetime


class IssueQuarantineRecord(BaseModel):
    record_id: UUID
    cleaning_run_id: UUID
    ingestion_job_id: int
    locator: str
    record_position: int
    bronze_version: str
    error_codes: list[str]
    issues: list[dict[str, Any]]
    entity: str
    source_table: str | None


class IssueStats(BaseModel):
    total: int
    assigned: int
    pending_verification: int
    verified: int


class AssigneeChoice(BaseModel):
    user_id: int
    username: str
    full_name: str


class IssueDepartmentChoice(BaseModel):
    department_id: int
    department_code: str
    department_name: str


class IssueDetail(IssueRead):
    quarantine_record_ids: list[UUID]
    quarantine_records: list[IssueQuarantineRecord]
    history: list[IssueEventRead]
