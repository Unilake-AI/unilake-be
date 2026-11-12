"""Quản lý quy tắc nguồn dùng cho bước làm sạch và chuẩn hóa."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

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
from app.modules.pipeline.models.profiles import (
    SilverDomainOwner,
    SilverProfileApproval,
    SilverProfileRevision,
    SilverSourceProfile,
)
from app.modules.pipeline.repositories.management_db import management_engine
from app.modules.pipeline.services.source_profiles import SourceProfile
from app.repositories.silver_governance_repo import (
    approved_evidence,
    lock_profile,
    reject,
    require_manager,
)
from app.repositories.silver_profile_repo import validate_profile_source

router = APIRouter(prefix="/pipeline/profiles", tags=["silver-profiles"])
DB = Annotated[Engine, Depends(management_engine)]
Admin = Annotated[dict[str, Any], Depends(require_role(RoleCode.ADMIN, RoleCode.DATA_ADMIN))]
SystemAdmin = Annotated[dict[str, Any], Depends(require_role(RoleCode.ADMIN))]
Reviewer = Annotated[
    dict[str, Any],
    Depends(
        require_role(
            RoleCode.ADMIN,
            RoleCode.DATA_ADMIN,
            RoleCode.ADMISSIONS_MANAGER,
            RoleCode.ACADEMIC_MANAGER,
            RoleCode.HR_MANAGER,
        )
    ),
]
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
    approval_status: str = "NEEDS_DOMAIN_OWNER_APPROVAL"


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
    for name, item in result.items():
        if item.storage == "database":
            try:
                with engine.begin() as connection:
                    approved_evidence(connection, name, item.fingerprint)
                item.approval_status = "APPROVED"
            except AppException:
                pass
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
            connection.execute(
                insert(SilverProfileRevision).values(
                    profile_id=name,
                    revision=new_revision,
                    definition=values["definition"],
                    fingerprint=values["fingerprint"],
                    created_by=actor,
                    created_at=values["updated_at"],
                )
            )
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


class OwnerGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    domain: str = Field(pattern=r"^(admissions|academic|hr)$")
    user_id: int = Field(gt=0)
    reason: str = Field(min_length=10, max_length=2000)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision: int = Field(gt=0)
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    grant_id: UUID
    evidence: str = Field(min_length=10, max_length=2000)
    allow_partial_publication: bool  # Explicit decision; no permissive default.


class RevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=10, max_length=2000)


@router.post("/owners/grants", status_code=201)
def grant_owner(request: OwnerGrant, db: DB, admin: SystemAdmin) -> dict[str, str]:
    grant_id = str(uuid4())
    with db.begin() as connection:
        require_manager(connection, str(request.user_id), request.domain)
        connection.execute(
            insert(SilverDomainOwner).values(
                grant_id=grant_id,
                domain=request.domain,
                user_id=str(request.user_id),
                granted_by=str(admin["sub"]),
                granted_at=datetime.now(UTC),
                reason=request.reason,
            )
        )
    return {"grant_id": grant_id}


@router.post("/owners/grants/{grant_id}/revoke")
def revoke_owner(
    grant_id: UUID, request: RevokeRequest, db: DB, admin: SystemAdmin
) -> dict[str, str]:
    with db.begin() as connection:
        changed = connection.execute(
            update(SilverDomainOwner)
            .where(
                SilverDomainOwner.grant_id == str(grant_id),
                SilverDomainOwner.revoked_at.is_(None),
            )
            .values(
                revoked_by=str(admin["sub"]),
                revoked_at=datetime.now(UTC),
                revocation_reason=request.reason,
            )
            .returning(SilverDomainOwner.grant_id)
        ).scalar_one_or_none()
        if changed is None:
            reject("SILVER_OWNER_GRANT_NOT_ACTIVE")
    return {"status": "REVOKED"}


@router.get("/{profile_id}/revisions")
def profile_history(profile_id: ProfileID, db: DB, reviewer: Reviewer) -> dict[str, Any]:
    with db.begin() as connection:
        current = lock_profile(connection, profile_id)
        if not set(reviewer["roles"]) & {"ADMIN", "DATA_ADMIN"}:
            require_manager(connection, str(reviewer["sub"]), current["definition"]["domain"])
        revisions = (
            connection.execute(
                select(SilverProfileRevision)
                .where(
                    SilverProfileRevision.profile_id == profile_id,
                )
                .order_by(SilverProfileRevision.revision)
            )
            .mappings()
            .all()
        )
        approvals = (
            connection.execute(
                select(SilverProfileApproval).where(
                    SilverProfileApproval.profile_id == profile_id,
                )
            )
            .mappings()
            .all()
        )
    return {
        "revisions": [dict(row) for row in revisions],
        "approvals": [dict(row) for row in approvals],
        "default_status": "NEEDS_DOMAIN_OWNER_APPROVAL",
    }


@router.post("/{profile_id}/approve", status_code=201)
def approve_profile(
    profile_id: ProfileID, request: ReviewRequest, db: DB, reviewer: Reviewer
) -> dict[str, str]:
    actor = str(reviewer["sub"])
    with db.begin() as connection:
        current = lock_profile(connection, profile_id)
        if (
            current["revision"] != request.expected_revision
            or current["fingerprint"] != request.expected_fingerprint
        ):
            reject("SILVER_PROFILE_REVISION_CONFLICT")
        if current["updated_by"] == actor:
            reject("SILVER_SELF_APPROVAL_FORBIDDEN", 403)
        require_manager(connection, actor, current["definition"]["domain"])
        # Profile lock serializes approval, revocation and updates.
        if connection.execute(
            select(SilverProfileApproval.revision).where(
                SilverProfileApproval.profile_id == profile_id,
                SilverProfileApproval.revision == request.expected_revision,
            )
        ).first():
            reject("SILVER_REVISION_ALREADY_REVIEWED")
        grant = (
            connection.execute(
                select(SilverDomainOwner)
                .where(
                    SilverDomainOwner.grant_id == str(request.grant_id),
                )
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if (
            grant is None
            or grant["revoked_at"] is not None
            or grant["user_id"] != actor
            or grant["domain"] != current["definition"]["domain"]
        ):
            reject("SILVER_DOMAIN_OWNER_REQUIRED", 403)
        connection.execute(
            insert(SilverProfileApproval).values(
                profile_id=profile_id,
                revision=request.expected_revision,
                grant_id=str(request.grant_id),
                approved_by=actor,
                approved_at=datetime.now(UTC),
                evidence=request.evidence,
                allow_partial_publication=request.allow_partial_publication,
            )
        )
    return {"status": "APPROVED"}


@router.post("/{profile_id}/revisions/{revision}/revoke")
def revoke_approval(
    profile_id: ProfileID, revision: int, request: RevokeRequest, db: DB, reviewer: Reviewer
) -> dict[str, str]:
    actor = str(reviewer["sub"])
    with db.begin() as connection:
        current = lock_profile(connection, profile_id)
        approval = (
            connection.execute(
                select(SilverProfileApproval)
                .where(
                    SilverProfileApproval.profile_id == profile_id,
                    SilverProfileApproval.revision == revision,
                )
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if approval is None or approval["revoked_at"] is not None:
            reject("SILVER_APPROVAL_NOT_ACTIVE")
        if "ADMIN" not in reviewer["roles"]:
            require_manager(connection, actor, current["definition"]["domain"])
            if approval["approved_by"] != actor:
                reject("SILVER_DOMAIN_OWNER_REQUIRED", 403)
        connection.execute(
            update(SilverProfileApproval)
            .where(
                SilverProfileApproval.profile_id == profile_id,
                SilverProfileApproval.revision == revision,
            )
            .values(
                revoked_by=actor, revoked_at=datetime.now(UTC), revocation_reason=request.reason
            )
        )
    return {"status": "REVOKED"}
