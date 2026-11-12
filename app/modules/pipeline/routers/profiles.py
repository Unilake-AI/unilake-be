"""Quản lý quy tắc nguồn dùng cho bước làm sạch và chuẩn hóa."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.deps import require_role
from app.core.exceptions import AppException
from app.core.roles import RoleCode
from app.modules.pipeline.models.profiles import SilverSourceProfile
from app.modules.pipeline.repositories.management_db import management_engine
from app.modules.pipeline.services.source_profiles import SourceProfile
from app.repositories.silver_profile_repo import validate_profile_source

router = APIRouter(prefix="/pipeline/profiles", tags=["silver-profiles"])
DB = Annotated[Engine, Depends(management_engine)]
Admin = Annotated[dict[str, Any], Depends(require_role(RoleCode.ADMIN, RoleCode.DATA_ADMIN))]
ProfileID = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")]


class ProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
    definition: SourceProfile


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    definition: SourceProfile


class ProfileRead(BaseModel):
    profile_id: str
    definition: SourceProfile
    fingerprint: str
    revision: int
    storage: str


def available_profiles(engine: Engine) -> dict[str, ProfileRead]:
    path = Path(settings.silver_profiles_path)
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(payload, dict):
        raise ValueError("INVALID_PROFILE_FILE")
    result = {}
    for name, definition in payload.items():
        profile = SourceProfile.model_validate(definition)
        result[name] = ProfileRead(
            profile_id=name,
            definition=profile,
            fingerprint=profile.fingerprint,
            revision=0,
            storage="file",
        )
    with engine.connect() as connection:
        for row in connection.execute(select(SilverSourceProfile)).mappings():
            result[row["profile_id"]] = ProfileRead(**dict(row), storage="database")
    return result


@router.get("", response_model=list[ProfileRead], summary="Danh sách cấu hình nguồn Silver")
def list_profiles(
    db: DB,
    _admin: Admin,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProfileRead]:
    profiles = available_profiles(db)
    return [profiles[name] for name in sorted(profiles)][offset : offset + limit]


@router.get(
    "/{profile_id}", response_model=ProfileRead, summary="Xem quy tắc làm sạch và chuẩn hóa"
)
def get_profile(profile_id: ProfileID, db: DB, _admin: Admin) -> ProfileRead:
    profile = available_profiles(db).get(profile_id)
    if profile is None:
        raise AppException(
            "Không tìm thấy cấu hình nguồn", code="SILVER_SOURCE_PROFILE_NOT_FOUND", status_code=404
        )
    return profile


def _save(
    db: Engine, name: str, definition: SourceProfile, actor: str, revision: int | None
) -> ProfileRead:
    validate_profile_source(db, definition)
    values = {
        "definition": definition.model_dump(mode="json"),
        "fingerprint": definition.fingerprint,
        "updated_by": actor[:100],
        "updated_at": datetime.now(UTC),
    }
    try:
        with db.begin() as connection:
            if revision is None:
                connection.execute(
                    insert(SilverSourceProfile).values(profile_id=name, revision=1, **values)
                )
                new_revision = 1
            else:
                changed = connection.execute(
                    update(SilverSourceProfile)
                    .where(
                        SilverSourceProfile.profile_id == name,
                        SilverSourceProfile.revision == revision,
                    )
                    .values(revision=revision + 1, **values)
                    .returning(SilverSourceProfile.profile_id)
                ).scalar_one_or_none()
                if changed is None:
                    raise AppException(
                        "Cấu hình đã thay đổi, hãy tải lại trước khi sửa",
                        code="SILVER_PROFILE_REVISION_CONFLICT",
                        status_code=409,
                    )
                new_revision = revision + 1
    except IntegrityError:
        raise AppException(
            "Mã cấu hình đã tồn tại", code="SILVER_PROFILE_CONFLICT", status_code=409
        ) from None
    return ProfileRead(
        profile_id=name,
        definition=definition,
        fingerprint=definition.fingerprint,
        revision=new_revision,
        storage="database",
    )


@router.post("", response_model=ProfileRead, status_code=201, summary="Tạo cấu hình nguồn Silver")
def create_profile(request: ProfileCreate, db: DB, admin: Admin) -> ProfileRead:
    if request.profile_id in available_profiles(db):
        raise AppException(
            "Mã cấu hình đã tồn tại", code="SILVER_PROFILE_CONFLICT", status_code=409
        )
    return _save(db, request.profile_id, request.definition, str(admin.get("sub", "")), None)


@router.put("/{profile_id}", response_model=ProfileRead, summary="Sửa cấu hình nguồn Silver")
def update_profile(
    profile_id: ProfileID, request: ProfileUpdate, db: DB, admin: Admin
) -> ProfileRead:
    current = get_profile(profile_id, db, admin)
    if current.revision != request.expected_revision:
        raise AppException(
            "Cấu hình đã thay đổi, hãy tải lại trước khi sửa",
            code="SILVER_PROFILE_REVISION_CONFLICT",
            status_code=409,
        )
    # Cấu hình từ file được lưu thành bản PostgreSQL khi quản trị viên sửa lần đầu.
    revision = None if current.storage == "file" else current.revision
    return _save(db, profile_id, request.definition, str(admin.get("sub", "")), revision)
