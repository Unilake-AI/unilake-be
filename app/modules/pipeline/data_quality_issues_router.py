"""Yêu cầu xử lý dữ liệu lỗi và lịch sử xác minh của từng phòng ban."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import false, insert, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.exceptions import AppException
from app.core.roles import DATA_WRITE_ROLES, RoleCode
from app.dependencies import get_db
from app.modules.pipeline.models import (
    CLEANING_RUNS,
    DATA_QUALITY_ISSUE_EVENTS,
    DATA_QUALITY_ISSUE_RECORDS,
    DATA_QUALITY_ISSUES,
    DATASET_CATALOG,
    QUARANTINE_RECORDS,
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


async def _execute(db: AsyncSession, statement: Any, params: dict[str, Any] | None = None) -> Any:
    try:
        return await db.execute(statement, params)
    except SQLAlchemyError:
        raise _error(
            "QUALITY_ISSUE_METADATA_UNAVAILABLE", "Không thể truy cập yêu cầu xử lý", 503
        ) from None


async def _active_roles(db: AsyncSession, user_id: int) -> set[str]:
    return set(
        (
            await _execute(
                db,
                text("""
        SELECT r.role_code FROM app.app_user u
        JOIN app.user_role ur ON ur.app_user_id = u.app_user_id
        JOIN app.role r ON r.role_id = ur.role_id
        WHERE u.app_user_id = :user_id AND u.is_active
    """),
                {"user_id": user_id},
            )
        ).scalars()
    )


async def _active_departments(db: AsyncSession, user_id: int) -> frozenset[int]:
    return frozenset(
        (
            await _execute(
                db,
                text("""
        SELECT a.department_id FROM app.app_user u
        JOIN app.employee e ON e.employee_id = u.employee_id
        JOIN app.assignment a ON a.employee_id = e.employee_id
        JOIN app.department d ON d.department_id = a.department_id
        WHERE u.app_user_id = :user_id AND u.is_active
          AND upper(e.employee_status) = 'ACTIVE'
          AND upper(a.assignment_status) = 'ACTIVE' AND d.status = 'ACTIVE'
          AND a.start_date <= (now() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date
          AND (a.end_date IS NULL OR a.end_date >= (now() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date)
    """),
                {"user_id": user_id},
            )
        ).scalars()
    )


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
    records = (
        await _execute(
            db,
            select(
                RECORD.record_id,
                RECORD.cleaning_run_id,
                RECORD.locator,
                RECORD.record_position,
                RECORD.error_codes,
                RECORD.issues,
                RUN.ingestion_job_id,
                RUN.bronze_version,
            )
            .select_from(DATA_QUALITY_ISSUE_RECORDS)
            .join(QUARANTINE_RECORDS, RECORD.record_id == LINK.record_id)
            .join(CLEANING_RUNS, RUN.run_id == RECORD.cleaning_run_id)
            .where(LINK.issue_id == issue["issue_id"])
            .order_by(RUN.started_at, RECORD.record_position, RECORD.record_id),
        )
    ).mappings()
    linked = [IssueQuarantineRecord.model_validate(dict(record)) for record in records]
    return IssueDetail(
        **issue,
        quarantine_record_ids=[record.record_id for record in linked],
        quarantine_records=linked,
        history=[IssueEventRead.model_validate(dict(row)) for row in rows],
    )


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
    rows = (
        (
            await _execute(
                db,
                select(RECORD.record_id, RUN.dataset_id, RUN.domain, DATASET.owner_department_id)
                .select_from(QUARANTINE_RECORDS)
                .join(CLEANING_RUNS, RECORD.cleaning_run_id == RUN.run_id)
                .join(DATASET_CATALOG, DATASET.dataset_id == RUN.dataset_id)
                .where(RECORD.record_id.in_(record_ids)),
            )
        )
        .mappings()
        .all()
    )
    if len(rows) != len(record_ids):
        raise _error("QUARANTINE_RECORD_NOT_FOUND", "Không tìm thấy một hoặc nhiều hồ sơ", 404)
    first = rows[0]
    if any(
        (row["dataset_id"], row["domain"]) != (first["dataset_id"], first["domain"]) for row in rows
    ):
        raise _error("QUALITY_ISSUE_DATASET_MISMATCH", "Các hồ sơ phải thuộc cùng dataset")
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


@router.get("", response_model=list[IssueRead])
async def list_issues(
    db: DB, actor: Actor, filters: Annotated[IssueFilters, Query()]
) -> list[IssueRead]:
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
    rows = (
        await _execute(
            db,
            query.order_by(ISSUE.updated_at.desc(), ISSUE.issue_id.desc())
            .limit(filters.limit)
            .offset(filters.offset),
        )
    ).mappings()
    return [IssueRead.model_validate(dict(row)) for row in rows]


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


@router.post("/{issue_id}/resolution", response_model=IssueDetail)
async def submit_resolution(
    issue_id: UUID, request: SubmitResolution, db: DB, actor: Actor
) -> IssueDetail:
    issue = await _issue(db, actor, issue_id, lock=True)
    # Kiểm tra người phụ trách trên dòng đã khóa. Quyền quản trị không cho phép nộp thay.
    if issue["assignee_user_id"] != actor.user_id or not actor.can_work_on(issue):
        raise _error("FORBIDDEN", "Chỉ người phụ trách đang có quyền mới được báo đã sửa", 403)
    if issue["status"] != "ASSIGNED":
        raise _error("QUALITY_ISSUE_STATE_CONFLICT", "Yêu cầu chưa ở trạng thái cần xử lý", 409)
    job = (
        await _execute(
            db,
            text("""
        SELECT j.ingestion_job_id
        FROM app.ingestion_job j JOIN app.data_layer l ON l.data_layer_id = j.target_layer_id
        WHERE j.ingestion_job_id = :job_id AND j.dataset_id = :dataset_id
          AND j.status = 'SUCCESS' AND j.job_type = 'EXECUTION_V1' AND l.layer_code = 'BRONZE'
          AND j.created_at > COALESCE(
              (SELECT previous.created_at FROM app.ingestion_job previous
               WHERE previous.ingestion_job_id = :previous_job_id),
              '-infinity'::timestamp
          )
          AND j.created_at > (
              SELECT max(old.created_at) FROM app.data_quality_issue_record ir
              JOIN app.quarantine_record q ON q.record_id = ir.record_id
              JOIN app.cleaning_run r ON r.run_id = q.cleaning_run_id
              JOIN app.ingestion_job old ON old.ingestion_job_id = r.ingestion_job_id
              WHERE ir.issue_id = :issue_id
          )
    """),
            {
                "job_id": request.ingestion_job_id,
                "dataset_id": issue["dataset_id"],
                "previous_job_id": issue["resolution_job_id"],
                "issue_id": str(issue_id),
            },
        )
    ).scalar_one_or_none()
    if job is None:
        raise _error("INVALID_RESOLUTION_JOB", "Cần job Bronze mới đã thành công của cùng dataset")
    return await _transition(
        db,
        actor,
        issue,
        status="PENDING_VERIFICATION",
        event_type="RESOLUTION_SUBMITTED",
        details={"ingestion_job_id": job, "note": request.note},
        resolution_job_id=job,
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
                select(RUN.cleaning_status, RUN.publication_status, RUN.finished_at).where(
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
