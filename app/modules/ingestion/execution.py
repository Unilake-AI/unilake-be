"""Điều phối nguồn → Bronze. Metadata hàng đợi được lưu trong PostgreSQL."""

from __future__ import annotations

import asyncio
import contextlib
import re
from typing import TYPE_CHECKING, Any, ParamSpec, Protocol, TypeVar

from app.core.exceptions import AppException
from app.modules.ingestion.contracts import BronzeWriteContext
from app.modules.ingestion.input_validator import ValidationBlockedError, required_rule_ids
from app.modules.ingestion.readers.request_builder import build_read_request
from app.modules.ingestion.validation_binding import bind_policy, pin_policy
from app.repositories.ingestion_repo import IngestionRepository, utcnow

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from app.modules.ingestion.contracts import CapturedArtifact
    from app.modules.ingestion.models import IngestionJob
    from app.modules.ingestion.readers.contract import ReadRequest
    from app.modules.ingestion.schemas import IngestionStart
    from app.repositories.ingestion_repo import SourceContext

P = ParamSpec("P")
R = TypeVar("R")


class CaptureReader(Protocol):
    """Cổng đọc cho dịch vụ thực thi; các DTO lưu trữ vẫn độc lập."""

    def read(self, request: ReadRequest) -> CapturedArtifact: ...


def authorize(user: dict[str, Any], *, write: bool = False) -> None:
    roles = user.get("roles", [])
    roles = [roles] if isinstance(roles, str) else roles
    allowed = {"ADMIN", "DATA_ADMIN"} if write else {"ADMIN", "DATA_ADMIN", "ANALYST", "VIEWER"}
    if not isinstance(roles, list | tuple | set) or allowed.isdisjoint(roles):
        raise AppException(
            "Không có quyền thực thi thao tác ingestion", code="FORBIDDEN", status_code=403
        )


def validate_context(context: SourceContext, source_table: str) -> ReadRequest:
    source, dataset, layer, system = context
    if not system or not system.is_active or not source.is_active:
        raise ValueError("SOURCE_INACTIVE")
    if not layer or layer.layer_code.upper() != "BRONZE":
        raise ValueError("BRONZE_DATASET_REQUIRED")
    if dataset.source_system_id != source.source_system_id or dataset.status != "ACTIVE":
        raise ValueError("DATASET_SOURCE_MISMATCH_OR_INACTIVE")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", dataset.domain):
        raise ValueError("INVALID_PARTITION_DOMAIN")
    try:
        request = build_read_request(source, source_table=source_table)
    except ValueError:
        raise ValueError("SOURCE_CONFIGURATION_INVALID_OR_UNSUPPORTED") from None
    if request.source_type in {"CSV", "EXCEL"}:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,199}", source_table):
            raise ValueError("INVALID_SOURCE_TABLE")
    elif not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?", source_table):
        raise ValueError("INVALID_SOURCE_TABLE")
    return request


async def run_io(function: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    """Hoàn tất I/O trên luồng trước khi giải phóng tệp tạm khi bị hủy."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await task
        raise


class IngestionExecutionService:
    def __init__(self, repository: IngestionRepository) -> None:
        self.repository = repository

    async def start(self, request: IngestionStart, user: dict[str, Any]) -> IngestionJob:
        authorize(user, write=True)
        repo = self.repository
        context = await repo.context(request.data_source_id, request.dataset_id, lock=True)
        existing = await repo.active(request.data_source_id)
        error = "SOURCE_ALREADY_RUNNING" if existing else None
        validation_input = None
        if not error:
            try:
                read_request = validate_context(context, request.source_table)
            except ValueError as exc:
                error = str(exc)
            if not error and request.validation_policy is not None:
                try:
                    validation_input = pin_policy(request.validation_policy, context, read_request)
                except (ValueError, TypeError, LookupError):
                    # Pydantic/codec errors có thể chứa đầu vào: chỉ đưa mã an toàn ra API.
                    error = "VALIDATION_POLICY_INVALID"
        job = await repo.create(
            data_source_id=request.data_source_id,
            dataset_id=request.dataset_id,
            target_layer_id=context[1].data_layer_id,
            source_table=request.source_table,
            requested_by=str(user.get("sub", ""))[:100],
            status="REJECTED" if error else "QUEUED",
            stage="VALIDATION" if error else "QUEUED",
            error_message=error,
            finished_at=utcnow() if error else None,
            validation_input=validation_input,
        )
        if error:
            raise AppException(
                "Không thể khởi chạy ingestion",
                code=error,
                status_code=409 if existing else 422,
                details={"ingestion_job_id": job.ingestion_job_id},
            )
        return job

    async def execute(
        self,
        job: IngestionJob,
        capture: Callable[[], AbstractContextManager[CaptureReader]],
        store: Callable[[CapturedArtifact, str, BronzeWriteContext], dict[str, Any]],
    ) -> None:
        """Chỉ gọi cho job đã được nhận xử lý; các bộ điều hợp chạy ngoài vòng lặp sự kiện."""
        repo = self.repository
        stage = "READING"
        job_id = job.ingestion_job_id
        try:
            if job.source_table is None or job.created_at is None:
                raise ValueError("INGESTION_JOB_CONTEXT_REQUIRED")
            context = await repo.context(job.data_source_id, job.dataset_id)
            request = validate_context(context, job.source_table)
            if job.validation_input is not None:
                request = bind_policy(job.validation_input, context, request, job_id)
            domain = context[1].domain
            # Thời điểm lấy dữ liệu khác thời điểm enqueue; không dùng created_at làm giả
            # thời gian capture khi job phải chờ lâu trong hàng đợi.
            write_context = BronzeWriteContext(
                ingestion_job_id=job_id,
                created_at=job.created_at,
                source_system_id=context[0].source_system_id,
                ingestion_timestamp=utcnow(),
            )
            # Kết thúc giao dịch đọc trước khi thực hiện I/O nguồn có thể kéo dài.
            await repo.db.commit()
            if request.validation_policy is not None:
                stage = "VALIDATING"
                await repo.stage(job_id, stage, 0)
            with capture() as captured:
                artifact = await run_io(captured.read, request)
                if request.validation_policy is not None:
                    report = artifact.validation_report
                    if (
                        report is None
                        or not report.can_accept
                        or report.ingestion_job_id != job_id
                        or report.policy_fingerprint != request.validation_policy.fingerprint
                        or {c.rule_id for c in report.checks if c.required}
                        != set(required_rule_ids(request.source_type))
                    ):
                        raise ValueError("VALIDATION_REPORT_REQUIRED")
                    # Nếu PostgreSQL không lưu được báo cáo, không gọi upload.
                    await repo.save_validation(job_id, report)
                stage = "WRITING"
                await repo.stage(job_id, stage, artifact.rows)
                output = await run_io(store, artifact, domain, write_context)
                stage = "COMMITTING"
                await repo.finish(job_id, output=output)
        except asyncio.CancelledError:
            # Để thời hạn giữ job tự hết hạn: I/O trên luồng có thể vẫn đang chạy.
            raise
        except ValidationBlockedError as exc:
            await repo.db.rollback()
            await repo.save_validation(job_id, exc.report)
            await repo.finish(job_id, error="BRONZE_INPUT_VALIDATION_BLOCKED")
        except Exception:
            # Ngoại lệ từ bộ kết nối có thể chứa thông tin xác thực hoặc giá trị dữ liệu nguồn.
            # Chỉ công khai các mã lỗi có kiểm soát theo từng giai đoạn.
            await repo.db.rollback()
            await repo.finish(job_id, error=f"INGESTION_{stage}_FAILED")
