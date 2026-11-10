"""Tìm bộ dữ liệu Bronze và lưu nguồn tải lên do hệ thống quản lý."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.core.exceptions import AppException
from app.modules.datasources.models import DataLayer, Dataset, DataSource, SourceSystem

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def upload_target(
    db: AsyncSession,
    dataset_id: int,
    source_type: str,
    actor: str,
    *,
    allowed_domains: frozenset[str] | None = None,
) -> tuple[DataSource, Dataset]:
    # Khóa bộ dữ liệu trong lúc tìm hoặc tạo nguồn tải lên để hai yêu cầu không tạo nguồn trùng.
    dataset = await db.scalar(
        select(Dataset).where(Dataset.dataset_id == dataset_id).with_for_update()
    )
    if dataset is None:
        raise AppException(
            "Bộ dữ liệu không tồn tại", code="UPLOAD_DATASET_NOT_FOUND", status_code=404
        )
    # Miền lấy từ bộ dữ liệu đã đăng ký, không lấy từ form do người dùng gửi lên.
    if allowed_domains is not None and dataset.domain not in allowed_domains:
        raise AppException(
            "Bạn chỉ được tải file vào bộ dữ liệu của phòng ban mình",
            code="UPLOAD_DOMAIN_FORBIDDEN",
            status_code=403,
        )
    layer = await db.get(DataLayer, dataset.data_layer_id)
    system = (
        await db.get(SourceSystem, dataset.source_system_id) if dataset.source_system_id else None
    )
    if (
        dataset.status != "ACTIVE"
        or not layer
        or layer.layer_code != "BRONZE"
        or not system
        or not system.is_active
        or not re.fullmatch(r"[a-z][a-z0-9_-]{0,49}", dataset.domain)
    ):
        raise AppException(
            "Cần chọn bộ dữ liệu Bronze đang hoạt động và có hệ thống nguồn",
            code="UPLOAD_DATASET_INVALID",
            status_code=422,
        )
    name = f"UPLOAD_{dataset_id}_{source_type}"
    source = await db.scalar(
        select(DataSource).where(
            DataSource.source_system_id == dataset.source_system_id,
            DataSource.source_name == name,
            DataSource.connection_type == "UPLOAD",
        )
    )
    if source is None:
        source = DataSource(
            source_system_id=dataset.source_system_id,
            source_name=name,
            source_type=source_type,
            connection_type="UPLOAD",
            location=None,
            configuration={"upload_dataset_id": dataset_id},
            is_active=True,
            ingestion_count=0,
            created_by=int(actor) if actor.isdecimal() else None,
        )
        db.add(source)
        await db.flush()
    if (
        not source.is_active
        or source.source_type != source_type
        or source.configuration.get("upload_dataset_id") != dataset_id
    ):
        raise AppException(
            "Nguồn tải lên đã bị tắt hoặc thay đổi", code="UPLOAD_SOURCE_INACTIVE", status_code=422
        )
    # Chưa tạo job và chưa công bố Bronze; kết thúc khóa trước khi lưu file có thể mất nhiều thời gian.
    await db.commit()
    return source, dataset
