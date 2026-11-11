"""Đọc đúng file đã tiếp nhận, kiểm tra toàn bộ nội dung rồi bàn giao cho bước lưu Bronze."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from zipfile import ZipFile

from openpyxl import load_workbook

from app.bronze_capture import BronzeCapture
from app.core.config import settings
from app.modules.ingestion.contracts import CapturedArtifact
from app.modules.ingestion.readers.contract import ReadRequest
from app.modules.ingestion.services.input_validator import (
    InputValidationError,
    InputValidator,
    ValidationBlockedError,
)
from app.modules.ingestion.validation import ValidationPolicy
from app.providers import upload_reader_client

if TYPE_CHECKING:
    from app.modules.ingestion.models import IngestionJob
    from app.repositories.ingestion_repo import SourceContext


class UploadValidator(InputValidator):
    """File mới chưa có cấu trúc đích: kiểm tra cột thực tế, không đoán ý nghĩa nghiệp vụ."""

    def columns(self, names: list[str]) -> None:
        if not names or any(not name.strip() for name in names):
            raise InputValidationError("IN-COM-006", "HEADER_EMPTY")
        if len(set(names)) != len(names):
            raise InputValidationError("IN-COM-006", "HEADER_DUPLICATE")
        if len(names) > self.policy.limits.max_columns:
            raise InputValidationError("IN-COM-005", "RESOURCE_LIMIT_EXCEEDED")
        self.pass_rules("IN-COM-006")


def upload_request(job: IngestionJob, context: SourceContext) -> ReadRequest:
    source, dataset, layer, system = context
    evidence = job.upload_input
    if not isinstance(evidence, dict):
        raise ValueError("UPLOAD_INPUT_REQUIRED")
    if (
        not source.is_active
        or source.connection_type != "UPLOAD"
        or not system
        or not system.is_active
        or dataset.status != "ACTIVE"
        or not layer
        or layer.layer_code != "BRONZE"
    ):
        raise ValueError("UPLOAD_CONTEXT_INACTIVE_OR_INVALID")
    expected = {
        "data_source_id": job.data_source_id,
        "dataset_id": job.dataset_id,
        "source_system_id": source.source_system_id,
        "domain": dataset.domain,
        "source_type": source.source_type,
        "filename": job.source_table,
    }
    if (
        any(evidence.get(key) != value for key, value in expected.items())
        or dataset.source_system_id != source.source_system_id
        or dataset.data_layer_id != job.target_layer_id
    ):
        raise ValueError("UPLOAD_BINDING_MISMATCH")
    uri = urlsplit(evidence.get("uri", ""))
    parts = uri.path.lstrip("/").split("/")
    if (
        uri.scheme != "s3"
        or uri.netloc != settings.minio_bucket_uploads
        or uri.query
        or uri.fragment
        or len(parts) != 4
        or parts[:2] != ["incoming", str(job.dataset_id)]
        or re.fullmatch(r"[a-f0-9]{32}", parts[2]) is None
        or parts[-1] != job.source_table
    ):
        raise ValueError("UPLOAD_LOCATION_INVALID")
    if (
        source.source_type not in {"CSV", "EXCEL"}
        or not isinstance(evidence.get("version_id"), str)
        or evidence["version_id"] in {"", "null"}
        or type(evidence.get("size_bytes")) is not int
        or not 0 < evidence["size_bytes"] <= settings.upload_max_bytes
        or not re.fullmatch(r"[a-f0-9]{64}", evidence.get("sha256", ""))
    ):
        raise ValueError("UPLOAD_EVIDENCE_INVALID")
    return ReadRequest(
        source.source_type,
        evidence["uri"],
        dict(evidence["parser_options"]),
        job.source_table,
        evidence["version_id"],
        ingestion_job_id=job.ingestion_job_id,
    )


class UploadCapture(BronzeCapture):
    def __init__(self, job: IngestionJob) -> None:
        self.job = job

    def read(self, request: ReadRequest) -> CapturedArtifact:
        evidence = self.job.upload_input
        if not isinstance(evidence, dict):
            raise ValueError("UPLOAD_INPUT_REQUIRED")
        options = dict(request.configuration)
        parser = {"kind": request.source_type, **options}
        if request.source_type == "EXCEL" and not parser.get("sheet_name"):
            parser["sheet_name"] = "first-sheet"
        limits = dict(evidence["limits"])
        # Giới hạn máy chủ vẫn có hiệu lực nếu cấu hình được siết lại sau khi nhận file.
        for name, current in {
            "max_file_bytes": settings.upload_max_bytes,
            "max_columns": settings.upload_max_columns,
            "max_records": settings.upload_max_records,
            "max_cell_chars": settings.upload_max_cell_chars,
            "timeout_seconds": settings.upload_timeout_seconds,
        }.items():
            limits[name] = min(limits[name], current)
        policy = ValidationPolicy.model_validate_json(
            json.dumps(
                {
                    "policy_id": "uploaded-file-structure",
                    "version": "1",
                    "source_system_id": evidence["source_system_id"],
                    "data_source_id": self.job.data_source_id,
                    "dataset_id": self.job.dataset_id,
                    "parser": parser,
                    "columns": [{"name": "uploaded-columns", "required": False}],
                    "limits": limits,
                }
            )
        )
        validator = UploadValidator(policy, self.job.ingestion_job_id)
        try:
            path = Path(self.temp.name) / str(request.source_table)
            client = upload_reader_client()
            uri = urlsplit(evidence["uri"])
            key = uri.path.lstrip("/")
            stat = client.stat_object(
                settings.minio_bucket_uploads, key, version_id=evidence["version_id"]
            )
            if stat.size != evidence["size_bytes"] or stat.version_id != evidence["version_id"]:
                raise InputValidationError("IN-COM-008", "SOURCE_CHANGED")
            response = client.get_object(
                settings.minio_bucket_uploads, key, version_id=evidence["version_id"]
            )
            started, size, digest = monotonic(), 0, hashlib.sha256()
            try:
                with path.open("wb") as output:
                    for chunk in response.stream(1024 * 1024):
                        size += len(chunk)
                        if (
                            size > evidence["size_bytes"]
                            or monotonic() - started > limits["timeout_seconds"]
                        ):
                            raise InputValidationError("IN-COM-005", "RESOURCE_LIMIT_EXCEEDED")
                        validator.budget(size=size)
                        digest.update(chunk)
                        output.write(chunk)
                if (
                    size != evidence["size_bytes"]
                    or digest.hexdigest() != evidence["sha256"]
                    or response.headers.get("x-amz-version-id") != evidence["version_id"]
                ):
                    raise InputValidationError("IN-COM-008", "SOURCE_CHANGED")
            finally:
                response.close()
                response.release_conn()
            if request.source_type == "EXCEL":
                # Chặn file Excel giải nén quá lớn trước khi mở bảng tính.
                with ZipFile(path) as archive:
                    if sum(item.file_size for item in archive.infolist()) > min(
                        evidence["max_expanded_bytes"], settings.upload_max_expanded_bytes
                    ):
                        raise InputValidationError("IN-COM-005", "RESOURCE_LIMIT_EXCEEDED")
                if not options.get("sheet_name"):
                    workbook = load_workbook(path, read_only=True, data_only=False)
                    try:
                        options["sheet_name"] = workbook.sheetnames[0]
                    finally:
                        workbook.close()
                    policy = policy.model_copy(
                        update={
                            "parser": policy.parser.model_copy(
                                update={"sheet_name": options["sheet_name"]}
                            )
                        }
                    )
                    validator = UploadValidator(policy, self.job.ingestion_job_id)
            validator.file(path)
            validator.pass_rules("IN-COM-008")
        except Exception as exc:
            raise ValidationBlockedError(validator.failure_report(exc)) from None
        report = validator.report()
        if not report.can_accept:
            raise ValidationBlockedError(report)
        # Bàn giao nguyên file đã kiểm tra, không dựng lại CSV/Excel từ các dòng đã đọc.
        return CapturedArtifact(
            path, validator.rows, evidence["sha256"], evidence["version_id"], report, options
        )
