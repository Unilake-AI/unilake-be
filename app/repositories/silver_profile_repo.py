"""Đọc cấu hình đã lưu; cấu hình có sẵn trong file vẫn dùng được."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from app.core.exceptions import AppException
from app.modules.datasources.models import DataLayer, Dataset, DataSource, SourceSystem
from app.modules.pipeline.profile_models import SilverSourceProfile
from app.modules.pipeline.source_profiles import SourceProfile, load_profile

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def load_source_profile(engine: Engine, path: str, profile_id: str) -> SourceProfile:
    with engine.connect() as connection:
        definition = connection.execute(
            select(SilverSourceProfile.definition).where(
                SilverSourceProfile.profile_id == profile_id
            )
        ).scalar_one_or_none()
    if definition is not None:
        return SourceProfile.model_validate(definition)
    return load_profile(path, profile_id)


def validate_profile_source(engine: Engine, profile: SourceProfile) -> None:
    with engine.connect() as connection:
        source = (
            connection.execute(
                select(
                    DataSource.source_system_id, DataSource.source_type, DataSource.is_active
                ).where(DataSource.data_source_id == profile.data_source_id)
            )
            .mappings()
            .first()
        )
        dataset = (
            connection.execute(
                select(
                    Dataset.source_system_id,
                    Dataset.domain,
                    Dataset.status,
                    DataLayer.layer_code,
                    SourceSystem.is_active,
                )
                .join(DataLayer, DataLayer.data_layer_id == Dataset.data_layer_id)
                .join(SourceSystem, SourceSystem.source_system_id == Dataset.source_system_id)
                .where(Dataset.dataset_id == profile.dataset_id)
            )
            .mappings()
            .first()
        )
    if (
        source is None
        or dataset is None
        or source["source_system_id"] != profile.source_system_id
        or dataset["source_system_id"] != profile.source_system_id
        or source["source_type"] != profile.source_type
        or not source["is_active"]
        or not dataset["is_active"]
        or dataset["domain"] != profile.domain
        or dataset["status"] != "ACTIVE"
        or dataset["layer_code"] != "BRONZE"
    ):
        raise AppException(
            "Cấu hình không khớp nguồn và bộ dữ liệu Bronze đang hoạt động",
            code="SILVER_PROFILE_SOURCE_MISMATCH",
            status_code=422,
        )
