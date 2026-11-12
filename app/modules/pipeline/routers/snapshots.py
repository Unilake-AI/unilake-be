"""Xem và lựa chọn phiên bản Silver đã được công bố."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.core.deps import require_role
from app.core.exceptions import AppException
from app.core.roles import RoleCode
from app.modules.pipeline.models.profiles import SilverActivation
from app.modules.pipeline.models.silver import SilverHead, SilverSnapshot
from app.modules.pipeline.repositories.management_db import management_engine
from app.modules.pipeline.repositories.silver import SilverRepository
from app.modules.pipeline.repositories.silver_storage import MinioSilverStore
from app.modules.pipeline.silver.publication import SilverPublicationService
from app.modules.pipeline.silver.validation import validate_snapshot
from app.providers import silver_writer_client
from app.repositories.silver_governance_repo import approval_required, authorize_manifest
from app.repositories.silver_profile_repo import load_source_profile

router = APIRouter(prefix="/pipeline/silver/snapshots", tags=["silver-snapshots"])
DB = Annotated[Engine, Depends(management_engine)]
Admin = Annotated[dict[str, Any], Depends(require_role(RoleCode.ADMIN, RoleCode.DATA_ADMIN))]
ProfileID = Annotated[str, Query(min_length=1, max_length=100)]


class ActivateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_current_snapshot: UUID | None


def publisher_for(db: Engine, profile_id: str) -> SilverPublicationService:
    profile = load_source_profile(db, settings.silver_profiles_path, profile_id)
    return SilverPublicationService(
        repository=SilverRepository(db),
        storage=MinioSilverStore(silver_writer_client(), settings.minio_bucket_silver),
        contracts=profile.contracts,
        scope=profile.scope,
        mode="production",
    )


@router.get("", summary="Danh sách phiên bản Silver theo cấu hình nguồn")
def list_snapshots(
    db: DB,
    _admin: Admin,
    profile_id: ProfileID,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    profile = load_source_profile(db, settings.silver_profiles_path, profile_id)
    scope = f"production:{profile.scope}"
    with db.connect() as connection:
        rows = (
            connection.execute(
                select(
                    SilverSnapshot.snapshot_id, SilverSnapshot.scope, SilverSnapshot.request_hash
                )
                .where(SilverSnapshot.scope == scope)
                .order_by(SilverSnapshot.snapshot_id)
                .limit(limit)
                .offset(offset)
            )
            .mappings()
            .all()
        )
    return {
        "scope": scope,
        "active_snapshot_id": SilverRepository(db).head(scope),
        "items": [dict(row) for row in rows],
    }


@router.get("/active", summary="Xem phiên bản Silver đang sử dụng")
def active_snapshot(db: DB, _admin: Admin, profile_id: ProfileID) -> dict[str, Any]:
    publisher = publisher_for(db, profile_id)
    active = publisher.repository.head(publisher.scope)
    return {
        "snapshot_id": active,
        "scope": publisher.scope,
        "manifest": publisher.describe_snapshot(active) if active else None,
    }


@router.get("/{snapshot_id}", summary="Xem thông tin và nguồn gốc phiên bản Silver")
def describe_snapshot(
    snapshot_id: UUID, db: DB, _admin: Admin, profile_id: ProfileID
) -> dict[str, Any]:
    return publisher_for(db, profile_id).describe_snapshot(str(snapshot_id))


@router.get("/{snapshot_id}/data", summary="Xem dữ liệu trong một bảng Silver")
def snapshot_data(
    snapshot_id: UUID,
    db: DB,
    _admin: Admin,
    profile_id: ProfileID,
    entity: Annotated[str, Query(pattern=r"^[a-z][a-z0-9_]*$")],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    tables = publisher_for(db, profile_id).read_snapshot(str(snapshot_id))
    if entity not in tables:
        raise AppException(
            "Không có bảng này trong phiên bản đã chọn",
            code="SILVER_ENTITY_NOT_FOUND",
            status_code=404,
        )
    rows = tables[entity]
    return {
        "snapshot_id": str(snapshot_id),
        "entity": entity,
        "total": len(rows),
        "limit": limit,
        "offset": offset,
        "items": rows[offset : offset + limit],
    }


@router.post("/{snapshot_id}/activate", summary="Chọn phiên bản Silver đang sử dụng")
def activate_snapshot(
    snapshot_id: UUID, request: ActivateSnapshot, db: DB, admin: Admin, profile_id: ProfileID
) -> dict[str, Any]:
    publisher = publisher_for(db, profile_id)
    tables = publisher.read_snapshot(str(snapshot_id))
    profile = load_source_profile(db, settings.silver_profiles_path, profile_id)
    specs = {
        entity: spec
        for contract in profile.contracts.values()
        for entity, spec in contract["$defs"].items()
    }
    if validate_snapshot(tables, specs):
        raise AppException(
            "Phiên bản không đạt kiểm tra dữ liệu Silver",
            code="SILVER_SNAPSHOT_INVALID",
            status_code=422,
        )
    expected = str(request.expected_current_snapshot) if request.expected_current_snapshot else None
    with db.begin() as connection:
        if approval_required():
            authorize_manifest(connection, publisher.describe_snapshot(str(snapshot_id)))
        condition = (
            SilverHead.snapshot_id == expected if expected else SilverHead.snapshot_id.is_(None)
        )
        changed = connection.execute(
            update(SilverHead)
            .where(SilverHead.scope == publisher.scope, condition)
            .values(snapshot_id=str(snapshot_id))
            .returning(SilverHead.scope)
        ).scalar_one_or_none()
        if changed is None:
            raise AppException(
                "Phiên bản hiện hành đã thay đổi, hãy tải lại",
                code="ACTIVE_SNAPSHOT_CHANGED",
                status_code=409,
            )
        connection.execute(
            insert(SilverActivation).values(
                activation_id=str(uuid4()),
                scope=publisher.scope,
                snapshot_id=str(snapshot_id),
                previous_snapshot_id=expected,
                activated_by=str(admin.get("sub", ""))[:100],
                activated_at=datetime.now(UTC),
            )
        )
    return {
        "snapshot_id": str(snapshot_id),
        "scope": publisher.scope,
        "previous_snapshot_id": expected,
    }
