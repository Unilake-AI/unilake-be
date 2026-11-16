"""Gợi ý người nạp từ bằng chứng Bronze và chờ quản trị duyệt trả hồ sơ lỗi."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.staff.schemas import (
    ApproveUploadReturn,
    AssigneeChoice,
    IssueDepartmentChoice,
    IssueDetail,
    UploadReturnPreview,
    UploadReturnSelection,
)
from app.modules.staff.services.quality_issues import (
    DEPARTMENT_ROLES,
    IssueActor,
    _active_roles,
    _detail,
    _error,
    _event,
    _linked_ids,
    _read,
    _require_admin,
    _transition,
)
from app.repositories import quality_issue_repo as repo

DEPARTMENT_CODES = {"hr": "PNS", "academic": "PDT", "admissions": "PTS"}


async def _routing(
    request: UploadReturnSelection, db: AsyncSession, actor: IssueActor, *, lock: bool = False
) -> tuple[UploadReturnPreview, dict[str, Any], dict[str, Any] | None]:
    _require_admin(actor)
    ids = {str(value) for value in request.quarantine_record_ids}
    if len(ids) != len(request.quarantine_record_ids):
        raise _error("DUPLICATE_QUARANTINE_RECORD", "Danh sách có hồ sơ trùng")
    rows = await _read(repo.issue_candidates(db, ids))
    if len(rows) != len(ids):
        raise _error("QUARANTINE_RECORD_NOT_FOUND", "Không tìm thấy hồ sơ đã chọn", 404)
    first = rows[0]
    if first["domain"] not in DEPARTMENT_CODES or any(
        any(
            row[key] != first[key]
            for key in (
                "dataset_id",
                "domain",
                "entity",
                "data_source_id",
                "source_table",
                "ingestion_job_id",
            )
        )
        for row in rows
    ):
        raise _error(
            "UPLOAD_RETURN_MIXED_SOURCE", "Hãy chọn hồ sơ của cùng một lần nạp và loại dữ liệu"
        )
    job = await _read(repo.upload_job(db, first["ingestion_job_id"]))
    if job is None or job.job_type != "UPLOAD_V1" or job.status != "SUCCESS":
        raise _error(
            "UPLOAD_RETURN_ORIGIN_REQUIRED",
            "Nguồn này không có lần tải file của người dùng để trả về",
        )
    root = await _read(repo.upload_job(db, job.root_job_id or job.ingestion_job_id, lock=lock))
    if (
        root is None
        or root.job_type != "UPLOAD_V1"
        or any(
            getattr(root, key) != getattr(job, key)
            for key in ("dataset_id", "data_source_id", "source_table")
        )
        or not root.requested_by
        or not root.requested_by.isdecimal()
        or not root.source_table
    ):
        raise _error(
            "UPLOAD_RETURN_ORIGIN_REQUIRED",
            "Chưa xác định được người nạp file gốc; cần quản trị kiểm tra",
        )
    user_id = int(root.requested_by)
    account = await _read(repo.uploader_account(db, user_id))
    if account is None or DEPARTMENT_ROLES[first["domain"]].isdisjoint(
        await _active_roles(db, user_id)
    ):
        raise _error(
            "UPLOAD_RETURN_USER_UNAVAILABLE",
            "Người nạp đã ngừng hoạt động hoặc không còn quyền xử lý lĩnh vực này",
        )
    department = await _read(
        repo.return_department(db, first["owner_department_id"], DEPARTMENT_CODES[first["domain"]])
    )
    if department is None:
        raise _error(
            "UPLOAD_RETURN_DEPARTMENT_UNAVAILABLE",
            "Chưa có phòng phụ trách đang hoạt động cho dữ liệu này",
        )
    existing_rows = await _read(repo.issues_for_records(db, ids))
    if lock and len(existing_rows) == 1:
        current = await _read(
            repo.get_issue(
                db,
                existing_rows[0]["issue_id"],
                is_admin=True,
                departments=actor.departments,
                domains=actor.domains,
                user_id=actor.user_id,
                lock=True,
            )
        )
        existing_rows = [current] if current else []
    existing = existing_rows[0] if len(existing_rows) == 1 else None
    if existing_rows and (
        existing is None
        or existing["status"] != "ASSIGNED"
        or existing["department_id"] != department["department_id"]
        or existing["assignee_user_id"] not in (None, user_id)
        or not {str(value) for value in await _linked_ids(db, existing["issue_id"])} <= ids
    ):
        raise _error(
            "UPLOAD_RETURN_EXISTING_REQUEST",
            "Hồ sơ đã thuộc yêu cầu khác. Hãy chọn đủ hồ sơ của yêu cầu cần trả về hoặc xem lại tại Chất lượng dữ liệu",
            409,
        )
    preview = UploadReturnPreview(
        department=IssueDepartmentChoice.model_validate(department),
        assignee=AssigneeChoice.model_validate(account),
        source_table=root.source_table,
        origin_upload_job_id=root.ingestion_job_id,
        existing_issue_id=existing["issue_id"] if existing else None,
        note="Chưa giao việc. Chỉ sau khi quản trị duyệt gửi, người nạp mới nhận hồ sơ của file này.",
    )
    return preview, first, existing


async def preview_return(
    request: UploadReturnSelection, db: AsyncSession, actor: IssueActor
) -> UploadReturnPreview:
    preview, _, _ = await _routing(request, db, actor)
    return preview


async def approve_return(
    request: ApproveUploadReturn, db: AsyncSession, actor: IssueActor
) -> IssueDetail:
    # Đọc lại người nạp và quyền khi duyệt; không tin người nhận do trình duyệt gửi lên.
    preview, first, existing = await _routing(request, db, actor, lock=True)
    assignment = {
        "assignee_user_id": preview.assignee.user_id,
        "assignment_mode": "SOURCE_UPLOADER",
        "origin_upload_job_id": preview.origin_upload_job_id,
    }
    if existing:
        additional = {str(value) for value in request.quarantine_record_ids} - {
            str(value) for value in await _linked_ids(db, existing["issue_id"])
        }
        if all(existing.get(key) == value for key, value in assignment.items()) and not additional:
            return await _detail(db, existing)
        if additional:
            await _read(repo.link_records(db, existing["issue_id"], additional))
        return await _transition(
            db,
            actor,
            existing,
            status="ASSIGNED",
            event_type="RETURN_APPROVED",
            details={
                **assignment,
                "previous_assignee_user_id": existing["assignee_user_id"],
                "added_record_ids": sorted(additional),
            },
            title=request.title,
            description=request.description,
            **assignment,
        )
    now = datetime.now(UTC)
    issue = {
        "issue_id": str(uuid4()),
        "dataset_id": first["dataset_id"],
        "department_id": preview.department.department_id,
        "domain": first["domain"],
        "title": request.title,
        "description": request.description,
        "status": "ASSIGNED",
        "created_by": actor.user_id,
        "resolution_job_id": None,
        "verification_run_id": None,
        "created_at": now,
        "updated_at": now,
        **assignment,
    }
    await _read(repo.insert_issue(db, issue))
    await _read(
        repo.link_records(
            db, issue["issue_id"], {str(value) for value in request.quarantine_record_ids}
        )
    )
    await _event(
        db,
        actor,
        issue["issue_id"],
        event_type="RETURN_APPROVED",
        from_status=None,
        to_status="ASSIGNED",
        details=assignment,
        now=now,
    )
    return await _detail(db, issue)
