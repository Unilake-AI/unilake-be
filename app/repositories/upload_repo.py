"""Tìm bộ dữ liệu Bronze và lưu nguồn tải lên do hệ thống quản lý."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

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
    # Chỉ lưu chính thức khi file đã được tiếp nhận và job đã tạo thành công.
    # Nếu bước sau gặp lỗi, bộ dữ liệu và nguồn vừa tạo cũng được hủy cùng yêu cầu.
    return source, dataset


async def create_upload_target(
    db: AsyncSession,
    dataset_name: str,
    source_type: str,
    actor: str,
    *,
    domain: str,
    description: str | None = None,
) -> tuple[DataSource, Dataset]:
    """Đăng ký bộ dữ liệu mới trong lần tải đầu, không yêu cầu người dùng biết mã nguồn."""
    layer = await db.scalar(select(DataLayer).where(DataLayer.layer_code == "BRONZE"))
    if layer is None:
        raise AppException(
            "Tầng Bronze chưa được thiết lập", code="UPLOAD_BRONZE_NOT_READY", status_code=422
        )

    # Lĩnh vực đã được kiểm tra theo quyền trước khi đến đây; không đoán từ tên file.
    # Nguồn này do hệ thống quản lý, người tải không cần khai báo kết nối hay đường dẫn.
    code = f"UPLOAD_{domain.upper()}"
    await db.execute(
        insert(SourceSystem)
        .values(
            source_code=code,
            source_name=f"File tải lên — {domain}",
            source_type="UPLOAD",
            is_active=True,
        )
        .on_conflict_do_nothing(index_elements=["source_code"])
    )
    system = await db.scalar(
        select(SourceSystem).where(SourceSystem.source_code == code).with_for_update()
    )
    if system is None or not system.is_active or system.source_type != "UPLOAD":
        raise AppException(
            "Nguồn tiếp nhận của lĩnh vực này chưa sẵn sàng",
            code="UPLOAD_SYSTEM_UNAVAILABLE",
            status_code=422,
        )

    # Hai yêu cầu tạo mới cùng lĩnh vực phải kiểm tra lần lượt để không tạo hai bộ trùng tên.
    # Chỉ chọn lại bộ cũ khi người dùng chủ động chọn; không tự gộp dữ liệu theo tên.
    name = unicodedata.normalize("NFC", " ".join(dataset_name.split()))
    duplicate = await db.scalar(
        select(Dataset.dataset_id).where(
            Dataset.source_system_id == system.source_system_id,
            Dataset.domain == domain,
            func.lower(Dataset.dataset_name) == name.lower(),
        )
    )
    if duplicate is not None:
        raise AppException(
            "Tên bộ dữ liệu đã có. Hãy chọn bộ dữ liệu đó hoặc đặt tên khác",
            code="UPLOAD_DATASET_NAME_CONFLICT",
            status_code=409,
        )
    dataset = Dataset(
        dataset_code=f"UPLOAD_{uuid4().hex}",
        dataset_name=name,
        domain=domain,
        source_system_id=system.source_system_id,
        data_layer_id=layer.data_layer_id,
        owner_department_id=system.owner_department_id,
        description=description,
        status="ACTIVE",
    )
    db.add(dataset)
    await db.flush()
    return await upload_target(db, dataset.dataset_id, source_type, actor)
