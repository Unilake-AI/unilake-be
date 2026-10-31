"""Cố định policy và vị trí nguồn khi enqueue; tuyệt đối không chụp mật khẩu vào job."""

from __future__ import annotations

import codecs
import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from app.modules.ingestion.readers.postgresql_reader import _required_port, _required_text
from app.modules.ingestion.validation import CSVOptions, PostgreSQLOptions, ValidationPolicy

if TYPE_CHECKING:
    from app.modules.ingestion.readers.contract import ReadRequest
    from app.repositories.ingestion_repo import SourceContext


def pin_policy(raw: dict[str, Any], context: SourceContext, request: ReadRequest) -> dict[str, Any]:
    policy = ValidationPolicy.model_validate_json(json.dumps(raw))
    source, dataset, _, _ = context
    if (policy.source_system_id, policy.data_source_id, policy.dataset_id, policy.parser.kind) != (
        source.source_system_id,
        source.data_source_id,
        dataset.dataset_id,
        request.source_type,
    ):
        raise ValueError("VALIDATION_POLICY_BINDING_MISMATCH")
    if isinstance(policy.parser, CSVOptions):
        codecs.lookup(policy.parser.encoding)
    if isinstance(policy.parser, PostgreSQLOptions):
        parser = policy.parser
        table = request.source_table or ""
        if "." not in table:
            table = "public." + table
        if table != f"{parser.schema_name}.{parser.table_name}":
            raise ValueError("VALIDATION_POLICY_TABLE_MISMATCH")
        for key in ("host", "database", "username", "password"):
            _required_text(request.configuration, key)
        _required_port(request.configuration)
        if (
            source.updated_at is None
            or request.configuration.get("connection_config_version")
            != parser.connection_config_version
        ):
            raise ValueError("CONNECTION_CONFIG_VERSION_REQUIRED")
    return {
        "policy": policy.model_dump(mode="json"),
        "fingerprint": policy.fingerprint,
        "location": request.location,
        "source_table": request.source_table,
        "source_updated_at": source.updated_at.isoformat() if source.updated_at else None,
    }


def bind_policy(
    snapshot: dict[str, Any], context: SourceContext, request: ReadRequest, job_id: int
) -> ReadRequest:
    policy = ValidationPolicy.model_validate_json(json.dumps(snapshot["policy"]))
    # Kiểm tra lại binding/active qua context, nhưng không thay parser bằng cấu hình mới.
    pinned = pin_policy(snapshot["policy"], context, request)
    if (
        policy.fingerprint != snapshot["fingerprint"]
        or snapshot["source_table"] != request.source_table
    ):
        raise ValueError("VALIDATION_SNAPSHOT_MISMATCH")
    if isinstance(policy.parser, PostgreSQLOptions) and (
        pinned["source_updated_at"] != snapshot["source_updated_at"]
    ):
        # Credentials vẫn lấy từ catalog hiện tại; đổi revision phải tạo job mới.
        raise ValueError("SOURCE_CONFIGURATION_CHANGED")
    return replace(
        request, location=snapshot["location"], validation_policy=policy, ingestion_job_id=job_id
    )
