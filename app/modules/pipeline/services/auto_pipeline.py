"""Quản lý cấu hình tự chạy; chỉ quản trị được bật quy định đã gắn đúng bộ dữ liệu."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from app.core.config import settings
from app.core.exceptions import AppException
from app.modules.pipeline.services.source_profiles import SourceProfile
from app.repositories.silver_governance_repo import approval_required, approved_evidence
from app.repositories.silver_profile_repo import load_source_profile, validate_profile_source

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def configure(
    engine: Engine, dataset_id: int, request: dict[str, Any], actor: str
) -> dict[str, Any]:
    saved_definition = None
    if not request["enabled"]:
        with engine.connect() as connection:
            saved = connection.execute(
                text("SELECT definition FROM app.auto_pipeline_policy WHERE dataset_id=:id"),
                {"id": dataset_id},
            ).scalar_one_or_none()
        if saved and saved.get("profile_id") == request["profile_id"]:
            saved_definition = saved["profile_definition"]
    # Luôn cho phép tắt cấu hình hiện có, kể cả khi nguồn đã bị ngừng hoạt động.
    # Muốn bật hoặc đổi profile vẫn phải kiểm tra lại nguồn đang dùng được.
    profile = (
        SourceProfile.model_validate(saved_definition)
        if saved_definition
        else load_source_profile(engine, settings.silver_profiles_path, request["profile_id"])
    )
    if saved_definition is None:
        validate_profile_source(engine, profile)
    if profile.dataset_id != dataset_id:
        raise AppException(
            "Quy định không thuộc bộ dữ liệu đã chọn",
            code="AUTO_PIPELINE_DATASET_MISMATCH",
            status_code=422,
        )
    definition = {
        **request,
        "profile_definition": profile.model_dump(mode="json"),
        "profile_fingerprint": profile.fingerprint,
    }
    definition.pop("expected_revision", None)
    definition.pop("enabled", None)
    with engine.begin() as connection:
        # Khóa bộ dữ liệu để cả lần tạo cấu hình đầu tiên cũng tránh ghi đè lẫn nhau.
        connection.execute(
            text("SELECT dataset_id FROM app.dataset WHERE dataset_id=:id FOR UPDATE"),
            {"id": dataset_id},
        )
        current = (
            connection.execute(
                text("SELECT * FROM app.auto_pipeline_policy WHERE dataset_id=:id FOR UPDATE"),
                {"id": dataset_id},
            )
            .mappings()
            .first()
        )
        revision = current["revision"] if current else 0
        if revision != request["expected_revision"]:
            raise AppException(
                "Cấu hình đã được người khác thay đổi",
                code="AUTO_PIPELINE_REVISION_CONFLICT",
                status_code=409,
            )
        if request["enabled"] and approval_required():
            approved_evidence(
                connection,
                request["profile_id"],
                profile.fingerprint,
                partial=request["allow_partial_publication"],
            )
        row = (
            connection.execute(
                text("""
            INSERT INTO app.auto_pipeline_policy(dataset_id,revision,enabled,definition,updated_by)
            VALUES (:id,:revision,:enabled,CAST(:definition AS jsonb),:actor)
            ON CONFLICT(dataset_id) DO UPDATE SET revision=EXCLUDED.revision,enabled=EXCLUDED.enabled,
                definition=EXCLUDED.definition,updated_by=EXCLUDED.updated_by,updated_at=now()
            RETURNING dataset_id,revision,enabled,definition,updated_by,updated_at
        """),
                {
                    "id": dataset_id,
                    "revision": revision + 1,
                    "enabled": request["enabled"],
                    "definition": json.dumps(definition),
                    "actor": actor,
                },
            )
            .mappings()
            .one()
        )
        return dict(row)
