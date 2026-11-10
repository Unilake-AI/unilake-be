"""Yêu cầu xử lý dữ liệu lỗi và lịch sử xác minh của từng phòng ban."""

from __future__ import annotations

import json
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypeVar
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from minio.error import MinioException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import false, func, insert, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from urllib3.exceptions import HTTPError

from app.core.deps import get_current_user
from app.core.exceptions import AppException
from app.core.roles import DATA_WRITE_ROLES, RoleCode
from app.dependencies import get_db
from app.modules.ingestion.contracts.uploads import (
    IngestionJobRead,
    receive_issue_upload,
    safe_upload_filename,
    supported_parser_options,
)
from app.modules.pipeline.models import (
    CLEANING_RUNS,
    DATA_QUALITY_ISSUE_EVENTS,
    DATA_QUALITY_ISSUE_RECORDS,
    DATA_QUALITY_ISSUES,
    DATASET_CATALOG,
    QUARANTINE_RECORDS,
)
from app.repositories import quality_issue_repo
from app.repositories.department_access_repo import (
    active_department_choices,
    active_departments,
    active_roles,
    eligible_assignees,
)

router = APIRouter(prefix="/data-quality-issues", tags=["data-quality-issues"])
DB = Annotated[AsyncSession, Depends(get_db)]
User = Annotated[dict[str, Any], Depends(get_current_user)]
ISSUE = DATA_QUALITY_ISSUES.c
LINK = DATA_QUALITY_ISSUE_RECORDS.c
EVENT = DATA_QUALITY_ISSUE_EVENTS.c
RUN = CLEANING_RUNS.c
RECORD = QUARANTINE_RECORDS.c
DATASET = DATASET_CATALOG.c
IssueStatus = Literal["ASSIGNED", "PENDING_VERIFICATION", "VERIFIED"]
DEPARTMENT_ROLES = {
    "admissions": {RoleCode.ADMISSIONS_MANAGER, RoleCode.ADMISSIONS_STAFF},
    "academic": {RoleCode.ACADEMIC_MANAGER, RoleCode.ACADEMIC_STAFF},
    "hr": {RoleCode.HR_MANAGER, RoleCode.HR_STAFF},
}
Result = TypeVar("Result")


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


@dataclass(frozen=True)
class IssueActor:
    user_id: int
    is_admin: bool
    departments: frozenset[int]
    domains: frozenset[str]

    def can_work_on(self, issue: dict[str, Any]) -> bool:
        return issue["department_id"] in self.departments and issue["domain"] in self.domains


def _error(code: str, message: str, status: int = 422) -> AppException:
    return AppException(message, code=code, status_code=status)


async def _read(operation: Awaitable[Result]) -> Result:
    try:
        return await operation
    except SQLAlchemyError:
        raise _error(
            "QUALITY_ISSUE_METADATA_UNAVAILABLE", "Không thể đọc bằng chứng xử lý", 503
        ) from None


async def _execute(db: AsyncSession, statement: Any, params: dict[str, Any] | None = None) -> Any:
    try:
        return await db.execute(statement, params)
    except SQLAlchemyError:
        raise _error(
            "QUALITY_ISSUE_METADATA_UNAVAILABLE", "Không thể truy cập yêu cầu xử lý", 503
        ) from None


async def _active_roles(db: AsyncSession, user_id: int) -> set[str]:
    try:
        return await active_roles(db, user_id)
    except SQLAlchemyError:
        raise _error(
            "QUALITY_ISSUE_METADATA_UNAVAILABLE", "Không thể đọc quyền hiện hành", 503
        ) from None


async def _active_departments(db: AsyncSession, user_id: int) -> frozenset[int]:
    try:
        return frozenset(
            row["department_id"] for row in await active_department_choices(db, user_id)
        )
    except SQLAlchemyError:
        raise _error(
            "QUALITY_ISSUE_METADATA_UNAVAILABLE", "Không thể đọc phân công phòng ban", 503
        ) from None


async def _actor(db: DB, user: User) -> IssueActor:
    try:
        user_id = int(user["sub"])
    except (KeyError, TypeError, ValueError):
        raise _error("INVALID_TOKEN", "Phiên đăng nhập không hợp lệ", 401) from None

    # Quyền hiện hành và phân công lấy từ DB, không lấy phòng ban từ request.
    roles = await _active_roles(db, user_id)
    roles.intersection_update(user["roles"])
    is_admin = not DATA_WRITE_ROLES.isdisjoint(roles)
    domains = frozenset(
        domain for domain, allowed in DEPARTMENT_ROLES.items() if not allowed.isdisjoint(roles)
    )
    if not is_admin and not domains:
        raise _error("FORBIDDEN", "Bạn không có quyền truy cập yêu cầu xử lý", 403)
    departments = await _active_departments(db, user_id) if domains else frozenset()
    return IssueActor(user_id, is_admin, departments, domains)


async def _validate_assignee(
    db: AsyncSession, user_id: int, department_id: int, domain: str
) -> None:
    # Vai trò đúng miền và phân công đang hiệu lực là điều kiện để nhận yêu cầu.
    roles = await _active_roles(db, user_id)
    if DEPARTMENT_ROLES[domain].isdisjoint(roles) or department_id not in await _active_departments(
        db, user_id
    ):
        raise _error(
            "INVALID_ISSUE_ASSIGNEE",
            "Người phụ trách phải có quyền nghiệp vụ và đang công tác tại phòng được giao",
        )


Actor = Annotated[IssueActor, Depends(_actor)]


def _require_admin(actor: IssueActor) -> None:
    if not actor.is_admin:
        raise _error("FORBIDDEN", "Thao tác yêu cầu quyền quản trị dữ liệu", 403)


async def _issue(
    db: AsyncSession, actor: IssueActor, issue_id: UUID, *, lock: bool = False
) -> dict[str, Any]:
    query = select(DATA_QUALITY_ISSUES).where(ISSUE.issue_id == str(issue_id))
    if not actor.is_admin:
        query = query.where(
            ISSUE.department_id.in_(actor.departments), ISSUE.domain.in_(actor.domains)
        )
    # Khóa yêu cầu trước khi kiểm tra trạng thái để hai người không cùng chuyển trạng thái.
    if lock:
        query = query.with_for_update()
    row = (await _execute(db, query)).mappings().one_or_none()
    if row is None:
        raise _error("QUALITY_ISSUE_NOT_FOUND", "Không tìm thấy yêu cầu xử lý", 404)
    return dict(row)


async def _linked_ids(db: AsyncSession, issue_id: str) -> list[UUID]:
    return [
        UUID(value)
        for value in (
            await _execute(
                db, select(LINK.record_id).where(LINK.issue_id == issue_id).order_by(LINK.record_id)
            )
        ).scalars()
    ]


async def _detail(db: AsyncSession, issue: dict[str, Any]) -> IssueDetail:
    rows = (
        await _execute(
            db,
            select(DATA_QUALITY_ISSUE_EVENTS)
            .where(EVENT.issue_id == issue["issue_id"])
            .order_by(EVENT.event_id),
        )
    ).mappings()
    # Phòng ban xem được lý do và vị trí lỗi trong yêu cầu đã giao, không đọc dòng Bronze qua API này.
    records = await _read(quality_issue_repo.issue_records(db, issue["issue_id"]))
    linked = [IssueQuarantineRecord.model_validate(dict(record)) for record in records]
    return IssueDetail(
        **issue,
        quarantine_record_ids=[record.record_id for record in linked],
        quarantine_records=linked,
        history=[IssueEventRead.model_validate(dict(row)) for row in rows],
    )


def _require_assignee(actor: IssueActor, issue: dict[str, Any]) -> None:
    if issue["assignee_user_id"] != actor.user_id or not actor.can_work_on(issue):
        raise _error("FORBIDDEN", "Chỉ người phụ trách đang có quyền mới được báo đã sửa", 403)
    if issue["status"] != "ASSIGNED":
        raise _error("QUALITY_ISSUE_STATE_CONFLICT", "Yêu cầu chưa ở trạng thái cần xử lý", 409)


async def _resolution_target(db: AsyncSession, issue_id: str) -> dict[str, Any]:
    rows = await _read(quality_issue_repo.issue_targets(db, issue_id))
    if len(rows) != 1:
        raise _error(
            "QUALITY_ISSUE_MULTIPLE_FILES",
            "Yêu cầu phải xác định một loại dữ liệu và file cần sửa",
            409,
        )
    return dict(rows[0])


async def _resolution_options(db: AsyncSession, issue_id: str) -> str:
    original = await _read(quality_issue_repo.latest_issue_output(db, issue_id))
    options = original.get("parser_options") if isinstance(original, dict) else None
    if not isinstance(options, dict):
        raise _error(
            "RESOLUTION_PARSER_REQUIRED",
            "Bản nguồn chưa lưu cách đọc; hãy chọn cách đọc file khi tải bản sửa",
        )
    if options.get("has_header") is False:
        raise _error(
            "RESOLUTION_HEADER_REQUIRED",
            "Nguồn cũ không có tiêu đề cột; cần quản trị thống nhất cách đọc trước khi sửa qua upload",
        )
    # Dùng cách đọc đã được ghi nhận của file lỗi, không tự đổi dấu phân cách hay sheet Excel.
    return json.dumps(supported_parser_options(options))


async def _event(
    db: AsyncSession,
    actor: IssueActor,
    issue_id: str,
    *,
    event_type: str,
    from_status: str | None,
    to_status: str,
    details: dict[str, Any],
    now: datetime,
) -> None:
    await _execute(
        db,
        insert(DATA_QUALITY_ISSUE_EVENTS).values(
            issue_id=issue_id,
            actor_id=actor.user_id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            details=details,
            created_at=now,
        ),
    )


async def _transition(
    db: AsyncSession,
    actor: IssueActor,
    issue: dict[str, Any],
    *,
    status: IssueStatus,
    event_type: str,
    details: dict[str, Any],
    **changes: Any,
) -> IssueDetail:
    now = datetime.now(UTC)
    # Trạng thái và lịch sử cùng giao dịch. Lỗi ghi sự kiện sẽ hoàn tác thay đổi trạng thái.
    values = {"status": status, "updated_at": now, **changes}
    await _execute(
        db, update(DATA_QUALITY_ISSUES).where(ISSUE.issue_id == issue["issue_id"]).values(**values)
    )
    await _event(
        db,
        actor,
        issue["issue_id"],
        event_type=event_type,
        from_status=issue["status"],
        to_status=status,
        details=details,
        now=now,
    )
    return await _detail(db, {**issue, **values})


@router.post("", status_code=201, response_model=IssueDetail)
async def create_issue(request: CreateIssue, db: DB, actor: Actor) -> IssueDetail:
    _require_admin(actor)
    record_ids = {str(record_id) for record_id in request.quarantine_record_ids}
    if len(record_ids) != len(request.quarantine_record_ids):
        raise _error("DUPLICATE_QUARANTINE_RECORD", "Danh sách có hồ sơ trùng")
    department = (
        await _execute(
            db,
            text(
                "SELECT department_id FROM app.department WHERE department_id = :id AND status = 'ACTIVE'"
            ),
            {"id": request.department_id},
        )
    ).scalar_one_or_none()
    if department is None:
        raise _error("INVALID_ISSUE_DEPARTMENT", "Phòng ban không tồn tại hoặc đã ngừng hoạt động")
    rows = await _read(quality_issue_repo.issue_candidates(db, record_ids))
    if len(rows) != len(record_ids):
        raise _error("QUARANTINE_RECORD_NOT_FOUND", "Không tìm thấy một hoặc nhiều hồ sơ", 404)
    first = rows[0]
    if any(
        any(
            row[field] != first[field]
            for field in ("dataset_id", "domain", "entity", "data_source_id", "source_table")
        )
        for row in rows
    ):
        raise _error(
            "QUALITY_ISSUE_DATASET_MISMATCH",
            "Các hồ sơ phải thuộc cùng bộ dữ liệu, loại dữ liệu và file nguồn",
        )
    if first["domain"] not in DEPARTMENT_ROLES or first["owner_department_id"] not in (
        None,
        department,
    ):
        raise _error("QUALITY_ISSUE_DEPARTMENT_MISMATCH", "Phòng ban không khớp với dataset")

    if request.assignee_user_id is not None:
        await _validate_assignee(db, request.assignee_user_id, department, first["domain"])

    now = datetime.now(UTC)
    issue = {
        "issue_id": str(uuid4()),
        "dataset_id": first["dataset_id"],
        "department_id": department,
        "domain": first["domain"],
        "title": request.title,
        "description": request.description,
        "status": "ASSIGNED",
        "created_by": actor.user_id,
        "assignee_user_id": request.assignee_user_id,
        "resolution_job_id": None,
        "verification_run_id": None,
        "created_at": now,
        "updated_at": now,
    }
    # Giao cho phòng ban ngay khi tạo. Chỉ lưu tham chiếu hồ sơ, không chép dòng Bronze.
    await _execute(db, insert(DATA_QUALITY_ISSUES).values(**issue))
    try:
        await db.execute(
            insert(DATA_QUALITY_ISSUE_RECORDS),
            [
                {"issue_id": issue["issue_id"], "record_id": record_id}
                for record_id in sorted(record_ids)
            ],
        )
    except SQLAlchemyError:
        raise _error(
            "QUALITY_ISSUE_METADATA_UNAVAILABLE", "Không thể lưu hồ sơ liên quan", 503
        ) from None
    await _event(
        db,
        actor,
        issue["issue_id"],
        event_type="ASSIGNED",
        from_status=None,
        to_status="ASSIGNED",
        details={"department_id": department, "assignee_user_id": request.assignee_user_id},
        now=now,
    )
    return await _detail(db, issue)


def _issue_query(actor: IssueActor, filters: IssueFilters) -> Any:
    query = select(DATA_QUALITY_ISSUES)
    if not actor.is_admin:
        query = (
            query.where(ISSUE.department_id.in_(actor.departments), ISSUE.domain.in_(actor.domains))
            if actor.departments
            else query.where(false())
        )
    if filters.department_id is not None:
        query = query.where(ISSUE.department_id == filters.department_id)
    if filters.assignee_user_id is not None:
        query = query.where(ISSUE.assignee_user_id == filters.assignee_user_id)
    if filters.status is not None:
        query = query.where(ISSUE.status == filters.status)
    if filters.mine_only:
        query = query.where(ISSUE.assignee_user_id == actor.user_id)
    return query


@router.get("", response_model=list[IssueRead])
async def list_issues(
    db: DB, actor: Actor, filters: Annotated[IssueFilters, Query()]
) -> list[IssueRead]:
    query = _issue_query(actor, filters)
    rows = (
        await _execute(
            db,
            query.order_by(ISSUE.updated_at.desc(), ISSUE.issue_id.desc())
            .limit(filters.limit)
            .offset(filters.offset),
        )
    ).mappings()
    return [IssueRead.model_validate(dict(row)) for row in rows]


@router.get("/stats", response_model=IssueStats, summary="Thống kê yêu cầu trong phạm vi được xem")
async def issue_stats(
    db: DB, actor: Actor, filters: Annotated[IssueFilters, Query()]
) -> IssueStats:
    items = _issue_query(actor, filters).subquery()
    row = (
        (
            await _execute(
                db,
                select(
                    func.count().label("total"),
                    func.count().filter(items.c.status == "ASSIGNED").label("assigned"),
                    func.count()
                    .filter(items.c.status == "PENDING_VERIFICATION")
                    .label("pending_verification"),
                    func.count().filter(items.c.status == "VERIFIED").label("verified"),
                ).select_from(items),
            )
        )
        .mappings()
        .one()
    )
    return IssueStats(**dict(row))


@router.get(
    "/departments",
    response_model=list[IssueDepartmentChoice],
    summary="Các phòng ban quản trị có thể giao xử lý",
)
async def issue_departments(db: DB, actor: Actor) -> list[IssueDepartmentChoice]:
    _require_admin(actor)
    rows = await _read(active_departments(db))
    return [IssueDepartmentChoice(**row) for row in rows]


@router.get(
    "/assignees",
    response_model=list[AssigneeChoice],
    summary="Chọn người phụ trách đủ quyền và còn phân công",
)
async def issue_assignees(
    db: DB,
    actor: Actor,
    department_id: Annotated[int, Query(gt=0)],
    domain: Literal["hr", "academic", "admissions"],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AssigneeChoice]:
    _require_admin(actor)
    rows = await _read(
        eligible_assignees(
            db, department_id, {str(role) for role in DEPARTMENT_ROLES[domain]}, limit, offset
        )
    )
    return [AssigneeChoice(**row) for row in rows]


@router.get("/{issue_id}", response_model=IssueDetail)
async def get_issue(issue_id: UUID, db: DB, actor: Actor) -> IssueDetail:
    return await _detail(db, await _issue(db, actor, issue_id))


@router.patch("/{issue_id}/assignment", response_model=IssueDetail)
async def assign_issue(issue_id: UUID, request: AssignIssue, db: DB, actor: Actor) -> IssueDetail:
    _require_admin(actor)
    issue = await _issue(db, actor, issue_id, lock=True)
    if issue["status"] != "ASSIGNED":
        raise _error(
            "QUALITY_ISSUE_STATE_CONFLICT", "Chỉ đổi người phụ trách khi yêu cầu cần xử lý", 409
        )
    await _validate_assignee(db, request.assignee_user_id, issue["department_id"], issue["domain"])
    previous = issue["assignee_user_id"]
    if previous == request.assignee_user_id:
        return await _detail(db, issue)
    # Ghi cả người cũ và người mới để truy vết việc bàn giao.
    return await _transition(
        db,
        actor,
        issue,
        status="ASSIGNED",
        event_type="ASSIGNEE_CHANGED",
        details={
            "previous_assignee_user_id": previous,
            "assignee_user_id": request.assignee_user_id,
        },
        assignee_user_id=request.assignee_user_id,
    )


@router.post(
    "/{issue_id}/uploads",
    response_model=IngestionJobRead,
    status_code=202,
    summary="Người phụ trách tải bản đã sửa vào đúng bộ dữ liệu của yêu cầu",
)
async def upload_resolution_file(
    issue_id: UUID,
    db: DB,
    actor: Actor,
    user: User,
    file: Annotated[UploadFile, File(description="Bản đã sửa, giữ tên file nguồn của yêu cầu")],
    parser_options: Annotated[
        str | None, Form(description="Bỏ trống để dùng cách đọc đã ghi nhận của file nguồn")
    ] = None,
) -> IngestionJobRead:
    try:
        issue = await _issue(db, actor, issue_id, lock=True)
        _require_assignee(actor, issue)
        target = await _resolution_target(db, str(issue_id))
        filename = safe_upload_filename(file.filename)
        if filename != target["source_table"]:
            raise _error(
                "RESOLUTION_FILE_MISMATCH", "Hãy tải đúng file cần sửa và giữ tên file nguồn"
            )
        # Bộ dữ liệu lấy từ yêu cầu đã giao; không nhận mã hay lĩnh vực tùy ý từ người tải.
        roles = await _active_roles(db, actor.user_id)
        roles.intersection_update(user.get("roles", []))
        options = (
            await _resolution_options(db, str(issue_id))
            if parser_options is None
            else parser_options
        )
        return await receive_issue_upload(
            db,
            file,
            issue["dataset_id"],
            options,
            {"sub": str(actor.user_id), "roles": sorted(roles)},
            quality_issue_id=str(issue_id),
        )
    except SQLAlchemyError:
        await db.rollback()
        raise _error(
            "INGESTION_QUEUE_UNAVAILABLE", "Không thể lưu lần tải file đã sửa", 503
        ) from None
    except (MinioException, HTTPError, OSError, ValueError):
        await db.rollback()
        raise _error("UPLOAD_STORAGE_UNAVAILABLE", "Không thể lưu file đã sửa", 503) from None
    finally:
        await file.close()


@router.post("/{issue_id}/resolution", response_model=IssueDetail)
async def submit_resolution(
    issue_id: UUID, request: SubmitResolution, db: DB, actor: Actor
) -> IssueDetail:
    issue = await _issue(db, actor, issue_id, lock=True)
    _require_assignee(actor, issue)
    target = await _resolution_target(db, str(issue_id))
    job = await _read(
        quality_issue_repo.resolution_job(
            db,
            request.ingestion_job_id,
            dataset_id=issue["dataset_id"],
            source_id=target["data_source_id"],
            filename=target["source_table"],
        )
    )
    if job is None:
        raise _error(
            "INVALID_RESOLUTION_JOB",
            "Cần lần nạp Bronze thành công, đúng bộ dữ liệu và file cần sửa",
        )
    root = await _read(
        quality_issue_repo.resolution_job(db, job.root_job_id or job.ingestion_job_id)
    )
    if root is None or root.created_at is None:
        raise _error("INVALID_RESOLUTION_JOB", "Không tìm thấy lần nạp gốc của file đã sửa")
    if job.job_type == "UPLOAD_V1":
        evidence = root.upload_input or {}
        if root.requested_by != str(actor.user_id) or evidence.get("quality_issue_id") not in (
            None,
            str(issue_id),
        ):
            raise _error(
                "INVALID_RESOLUTION_OWNER", "File phải do người phụ trách tải lên cho yêu cầu này"
            )
    old_time = await _read(quality_issue_repo.latest_invalid_created_at(db, str(issue_id)))
    if old_time is None or root.created_at <= old_time:
        raise _error(
            "INVALID_RESOLUTION_JOB",
            "Cần tải bản mới sau lần nạp có lỗi; chạy lại file cũ chưa phải bản sửa",
        )
    if issue["resolution_job_id"] is not None:
        previous = await _read(quality_issue_repo.resolution_job(db, issue["resolution_job_id"]))
        previous_root = (
            await _read(
                quality_issue_repo.resolution_job(
                    db, previous.root_job_id or previous.ingestion_job_id
                )
            )
            if previous is not None
            else None
        )
        if (
            previous_root is None
            or previous_root.created_at is None
            or root.created_at <= previous_root.created_at
        ):
            raise _error("INVALID_RESOLUTION_JOB", "Lần nộp tiếp theo phải dùng một bản tải mới")
    return await _transition(
        db,
        actor,
        issue,
        status="PENDING_VERIFICATION",
        event_type="RESOLUTION_SUBMITTED",
        details={"ingestion_job_id": job.ingestion_job_id, "note": request.note},
        resolution_job_id=job.ingestion_job_id,
        verification_run_id=None,
    )


@router.post("/{issue_id}/verification", response_model=IssueDetail)
async def verify_issue(
    issue_id: UUID, request: SubmitVerification, db: DB, actor: Actor
) -> IssueDetail:
    _require_admin(actor)
    issue = await _issue(db, actor, issue_id, lock=True)
    if issue["status"] != "PENDING_VERIFICATION":
        raise _error("QUALITY_ISSUE_STATE_CONFLICT", "Yêu cầu chưa chờ xác minh", 409)
    run = (
        (
            await _execute(
                db,
                select(
                    RUN.cleaning_status, RUN.publication_status, RUN.finished_at, RUN.entity
                ).where(
                    RUN.run_id == str(request.cleaning_run_id),
                    RUN.ingestion_job_id == issue["resolution_job_id"],
                    RUN.dataset_id == issue["dataset_id"],
                ),
            )
        )
        .mappings()
        .one_or_none()
    )
    if run is None or run["finished_at"] is None:
        raise _error("INVALID_VERIFICATION_RUN", "Cần lượt Cleaning đã kết thúc của đúng job sửa")
    target = await _resolution_target(db, str(issue_id))
    if run["entity"] != target["entity"]:
        raise _error("INVALID_VERIFICATION_RUN", "Lượt kiểm tra phải đúng loại dữ liệu cần sửa")
    confirmed = set(request.confirmed_record_ids)
    if request.verified:
        if run["cleaning_status"] != "COMPLETED" or run["publication_status"] not in {
            "PUBLISHED",
            "PUBLISHED_WITH_QUARANTINE",
        }:
            raise _error("VERIFICATION_RUN_NOT_PUBLISHED", "Lượt chạy chưa xuất bản Silver")
        # Admin đối chiếu theo khóa nghiệp vụ. Locator cũ chỉ đúng trong phiên bản Bronze cũ.
        if confirmed != set(await _linked_ids(db, str(issue_id))):
            raise _error("VERIFICATION_RECORDS_INCOMPLETE", "Cần xác nhận đủ các hồ sơ liên quan")
    elif confirmed:
        raise _error("VERIFICATION_RECORDS_UNEXPECTED", "Không gửi hồ sơ đã xác nhận khi từ chối")
    return await _transition(
        db,
        actor,
        issue,
        status="VERIFIED" if request.verified else "ASSIGNED",
        event_type="VERIFIED" if request.verified else "VERIFICATION_REJECTED",
        details={
            "cleaning_run_id": str(request.cleaning_run_id),
            "note": request.note,
            "confirmed_record_ids": sorted(str(value) for value in confirmed),
        },
        verification_run_id=str(request.cleaning_run_id),
    )
