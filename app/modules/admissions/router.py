"""Admissions workflow API. All mutations recheck current database roles and scope."""

from __future__ import annotations

import csv
from io import StringIO
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.exceptions import AppException
from app.dependencies import get_db
from app.modules.admissions.contracts import CorrectionCreate  # noqa: TC001
from app.modules.admissions.policy import AdmissionRuleError
from app.repositories import admission_api_repo as repo

router = APIRouter(prefix="/admissions", tags=["admissions"])
DB = Annotated[AsyncSession, Depends(get_db)]
User = Annotated[dict[str, Any], Depends(get_current_user)]


async def _run(db: AsyncSession, callback: Any) -> Any:
    try:
        return await db.run_sync(lambda session: callback(session.connection()))
    except AdmissionRuleError as exc:
        code = exc.code
        status = (
            403
            if code.endswith("FORBIDDEN")
            else 404
            if code.endswith("NOT_FOUND")
            else 409
            if "CONFLICT" in code or "NOT_PENDING" in code
            else 422
        )
        raise AppException(
            exc.message,
            code=code,
            status_code=status,
            details={"field": exc.field, **exc.details},
        ) from None


@router.get("/registrations", summary="Tra đăng ký và revision hiện tại theo nguồn")
async def registrations(
    db: DB,
    user: User,
    dataset_id: Annotated[int, Query(gt=0)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    applicant_code: Annotated[str | None, Query(max_length=200)] = None,
    program_code: Annotated[str | None, Query(max_length=30)] = None,
) -> Any:
    return await _run(
        db,
        lambda conn: repo.registrations(
            conn,
            user,
            dataset_id,
            limit=limit,
            offset=offset,
            applicant_code=applicant_code,
            program_code=program_code,
        ),
    )


@router.get("/registrations/export", summary="Tải CSV có mã tham chiếu và expected_revision")
async def export_registrations(
    db: DB,
    user: User,
    dataset_id: Annotated[int, Query(gt=0)],
    workflow: Literal["results", "enrollment"],
    limit: Annotated[int, Query(ge=1, le=10000)] = 10000,
    offset: Annotated[int, Query(ge=0)] = 0,
    applicant_code: Annotated[str | None, Query(max_length=200)] = None,
    program_code: Annotated[str | None, Query(max_length=30)] = None,
) -> Response:
    page = await _run(
        db,
        lambda conn: repo.registrations(
            conn,
            user,
            dataset_id,
            limit=limit,
            offset=offset,
            applicant_code=applicant_code,
            program_code=program_code,
            workflow=workflow,
        ),
    )
    fields = ["applicant_code", "program_code", "year_label", "method_code", "expected_revision"]
    fields += (
        ["admission_score", "admission_status"]
        if workflow == "results"
        else ["enrollment_status", "enrollment_date"]
    )
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in page["items"]:
        values = {n: row.get(n) for n in fields}
        values["expected_revision"] = row["revision"]
        writer.writerow(values)
    return Response(
        output.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="admissions-{workflow}-{offset}.csv"',
            "X-Total-Count": str(page["total"]),
            "X-Next-Offset": str(offset + len(page["items"]))
            if offset + len(page["items"]) < page["total"]
            else "",
        },
    )


@router.get("/registrations/{admission_id}/history", summary="Lịch sử thay đổi kết quả và nhập học")
async def admission_history(
    admission_id: int,
    db: DB,
    user: User,
    dataset_id: Annotated[int, Query(gt=0)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Any:
    return await _run(
        db, lambda conn: repo.history(conn, user, dataset_id, admission_id, limit, offset)
    )


@router.post(
    "/corrections", status_code=201, summary="Manager/Admin tạo và xem trước yêu cầu đính chính"
)
async def create_correction(request: CorrectionCreate, db: DB, user: User) -> Any:
    return await _run(db, lambda conn: repo.create_correction(conn, user, request))


@router.get("/corrections/{correction_id}", summary="Xem yêu cầu đính chính đã tạo")
async def correction(correction_id: UUID, db: DB, user: User) -> Any:
    return await _run(db, lambda conn: repo.correction(conn, user, str(correction_id))[1])


@router.post(
    "/corrections/{correction_id}/apply",
    summary="Manager/Admin xác nhận áp dụng yêu cầu đính chính",
)
async def apply_correction(correction_id: UUID, db: DB, user: User) -> Any:
    return await _run(db, lambda conn: repo.apply_correction(conn, user, str(correction_id)))
