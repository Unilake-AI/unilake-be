"""Xử lý yêu cầu sửa dữ liệu, phân công và bằng chứng xác minh."""

from __future__ import annotations

import json
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, TypeVar
from uuid import UUID, uuid4

from fastapi import UploadFile
from minio.error import MinioException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from urllib3.exceptions import HTTPError

from app.core.exceptions import AppException
from app.core.roles import DATA_WRITE_ROLES, RoleCode
from app.modules.ingestion.contracts.uploads import (
    receive_issue_upload,
    safe_upload_filename,
    supported_parser_options,
)
from app.modules.staff.schemas import (
    AssigneeChoice,
    AssignIssue,
    CreateIssue,
    IssueDepartmentChoice,
    IssueDetail,
    IssueEventRead,
    IssueFilters,
    IssueQuarantineRecord,
    IssueRead,
    IssueStats,
    IssueStatus,
    SubmitResolution,
    SubmitVerification,
)
from app.repositories import quality_issue_repo
from app.repositories.department_access_repo import (
    active_department_choices,
    active_departments,
    active_roles,
    eligible_assignees,
)

if TYPE_CHECKING:
    from app.modules.ingestion.contracts.jobs import IngestionJobRead

DEPARTMENT_ROLES = {
    "admissions": {RoleCode.ADMISSIONS_MANAGER, RoleCode.ADMISSIONS_STAFF},
    "academic": {RoleCode.ACADEMIC_MANAGER, RoleCode.ACADEMIC_STAFF},
    "hr": {RoleCode.HR_MANAGER, RoleCode.HR_STAFF},
}
Result = TypeVar("Result")


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


async def issue_actor(db: AsyncSession, user: dict[str, Any]) -> IssueActor:
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


def _require_admin(actor: IssueActor) -> None:
    if not actor.is_admin:
        raise _error("FORBIDDEN", "Thao tác yêu cầu quyền quản trị dữ liệu", 403)


async def _issue(
    db: AsyncSession, actor: IssueActor, issue_id: UUID, *, lock: bool = False
) -> dict[str, Any]:
    row = await _read(quality_issue_repo.get_issue(db, str(issue_id), **_scope(actor), lock=lock))
    if row is None:
        raise _error("QUALITY_ISSUE_NOT_FOUND", "Không tìm thấy yêu cầu xử lý", 404)
    return row


async def _linked_ids(db: AsyncSession, issue_id: str) -> list[UUID]:
    return [
        UUID(value) for value in await _read(quality_issue_repo.linked_record_ids(db, issue_id))
    ]


async def _detail(db: AsyncSession, issue: dict[str, Any]) -> IssueDetail:
    history = await _read(quality_issue_repo.issue_history(db, issue["issue_id"]))
    # Chỉ trả lý do và vị trí lỗi thuộc yêu cầu đã giao, không trả nội dung file gốc.
    records = await _read(quality_issue_repo.issue_records(db, issue["issue_id"]))
    linked = [IssueQuarantineRecord.model_validate(record) for record in records]
    return IssueDetail(
        **issue,
        quarantine_record_ids=[record.record_id for record in linked],
        quarantine_records=linked,
        history=[IssueEventRead.model_validate(row) for row in history],
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
    await _read(
        quality_issue_repo.add_event(
            db,
            {
                "issue_id": issue_id,
                "actor_id": actor.user_id,
                "event_type": event_type,
                "from_status": from_status,
                "to_status": to_status,
                "details": details,
                "created_at": now,
            },
        )
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
    await _read(quality_issue_repo.update_issue(db, issue["issue_id"], values))
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


async def create_issue(request: CreateIssue, db: AsyncSession, actor: IssueActor) -> IssueDetail:
    _require_admin(actor)
    record_ids = {str(record_id) for record_id in request.quarantine_record_ids}
    if len(record_ids) != len(request.quarantine_record_ids):
        raise _error("DUPLICATE_QUARANTINE_RECORD", "Danh sách có hồ sơ trùng")
    department = await _read(quality_issue_repo.active_department_id(db, request.department_id))
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
    await _read(quality_issue_repo.insert_issue(db, issue))
    await _read(quality_issue_repo.link_records(db, issue["issue_id"], record_ids))
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


def _scope(actor: IssueActor, filters: IssueFilters | None = None) -> dict[str, Any]:
    scope: dict[str, Any] = {
        "is_admin": actor.is_admin,
        "departments": actor.departments,
        "domains": actor.domains,
        "user_id": actor.user_id,
    }
    if filters is not None:
        scope.update(filters.model_dump(exclude={"limit", "offset"}))
    return scope


async def list_issues(
    db: AsyncSession, actor: IssueActor, filters: IssueFilters
) -> list[IssueRead]:
    rows = await _read(
        quality_issue_repo.list_issues(
            db, limit=filters.limit, offset=filters.offset, **_scope(actor, filters)
        )
    )
    return [IssueRead.model_validate(row) for row in rows]


async def issue_stats(db: AsyncSession, actor: IssueActor, filters: IssueFilters) -> IssueStats:
    values = await _read(quality_issue_repo.issue_stats(db, **_scope(actor, filters)))
    return IssueStats(**values)


async def issue_departments(db: AsyncSession, actor: IssueActor) -> list[IssueDepartmentChoice]:
    _require_admin(actor)
    rows = await _read(active_departments(db))
    return [IssueDepartmentChoice(**row) for row in rows]


async def issue_assignees(
    db: AsyncSession,
    actor: IssueActor,
    department_id: int,
    domain: Literal["hr", "academic", "admissions"],
    limit: int = 20,
    offset: int = 0,
) -> list[AssigneeChoice]:
    _require_admin(actor)
    rows = await _read(
        eligible_assignees(
            db, department_id, {str(role) for role in DEPARTMENT_ROLES[domain]}, limit, offset
        )
    )
    return [AssigneeChoice(**row) for row in rows]


async def get_issue(issue_id: UUID, db: AsyncSession, actor: IssueActor) -> IssueDetail:
    return await _detail(db, await _issue(db, actor, issue_id))


async def assign_issue(
    issue_id: UUID, request: AssignIssue, db: AsyncSession, actor: IssueActor
) -> IssueDetail:
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


async def upload_resolution_file(
    issue_id: UUID,
    db: AsyncSession,
    actor: IssueActor,
    user: dict[str, Any],
    file: UploadFile,
    parser_options: str | None = None,
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


async def submit_resolution(
    issue_id: UUID, request: SubmitResolution, db: AsyncSession, actor: IssueActor
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


async def verify_issue(
    issue_id: UUID, request: SubmitVerification, db: AsyncSession, actor: IssueActor
) -> IssueDetail:
    _require_admin(actor)
    issue = await _issue(db, actor, issue_id, lock=True)
    if issue["status"] != "PENDING_VERIFICATION":
        raise _error("QUALITY_ISSUE_STATE_CONFLICT", "Yêu cầu chưa chờ xác minh", 409)
    run = await _read(
        quality_issue_repo.verification_run(
            db,
            str(request.cleaning_run_id),
            issue["resolution_job_id"],
            issue["dataset_id"],
        )
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
