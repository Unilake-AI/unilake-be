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
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.core.deps import require_role
from app.core.exceptions import AppException
from app.core.roles import MANAGER_SCOPES, RoleCode
from app.modules.pipeline.repositories.management_db import management_engine
from app.modules.pipeline.repositories.object_metadata import configuration, edit_configuration
from app.modules.pipeline.services.profile_storage import save_profile
from app.modules.pipeline.services.source_profiles import SourceProfile
from app.repositories.silver_governance_repo import (
    approved_evidence,
    lock_profile,
    reject,
    require_manager,
)

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
        for row in configuration(connection)["profiles"].values():
            result[row["profile_id"]] = ProfileRead(
                **dict(row), storage="database" if connection.dialect.name == "sqlite" else "minio"
            )
    for name, item in result.items():
        if item.storage != "file":
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
    dataset_id: Annotated[int | None, Query(gt=0)] = None,
) -> list[ProfileRead]:
    profiles = available_profiles(db)
    # Lọc trước khi chia trang để giao diện chỉ chọn cách xử lý thuộc đúng bộ dữ liệu.
    matched = [
        profiles[name]
        for name in sorted(profiles)
        if dataset_id is None or profiles[name].definition.dataset_id == dataset_id
    ]
    return matched[offset : offset + limit]


class ReviewGrant(BaseModel):
    grant_id: UUID
    domain: str


class ReviewProfile(BaseModel):
    profile_id: str
    definition: SourceProfile
    revision: int
    fingerprint: str
    status: str
    can_approve: bool
    can_revoke: bool


class ReviewQueue(BaseModel):
    grants: list[ReviewGrant]
    items: list[ReviewProfile]


def reviewer_grants(state: dict[str, Any], reviewer: dict[str, Any]) -> list[ReviewGrant]:
    actor = str(reviewer["sub"])
    domains = {MANAGER_SCOPES[role][0] for role in reviewer["roles"] if role in MANAGER_SCOPES}
    return [
        ReviewGrant(grant_id=grant["grant_id"], domain=grant["domain"])
        for grant in state["grants"].values()
        if str(grant["user_id"]) == actor
        and grant["revoked_at"] is None
        and grant["domain"] in domains
    ]


@router.get("/review-queue", response_model=ReviewQueue)
def review_queue(db: DB, reviewer: Reviewer) -> ReviewQueue:
    """Chỉ trả các cấu hình đã đăng ký thuộc domain có grant còn hiệu lực."""
    with db.begin() as connection:
        state = configuration(connection)
        grants = reviewer_grants(state, reviewer)
        domains = {grant.domain for grant in grants}
        for domain in sorted(domains):
            require_manager(connection, str(reviewer["sub"]), domain)
        items = []
        for name, current in sorted(state["profiles"].items()):
            if current["definition"]["domain"] not in domains:
                continue
            approval = state["approvals"].get(f"{name}:{current['revision']}")
            status = (
                "PENDING"
                if approval is None
                else "REVOKED"
                if approval["revoked_at"] is not None
                else "APPROVED"
            )
            items.append(
                ReviewProfile(
                    profile_id=name,
                    definition=current["definition"],
                    revision=current["revision"],
                    fingerprint=current["fingerprint"],
                    status=status,
                    can_approve=approval is None
                    and str(current["updated_by"]) != str(reviewer["sub"]),
                    can_revoke=status == "APPROVED"
                    and str(approval["approved_by"]) == str(reviewer["sub"]),
                )
            )
    return ReviewQueue(grants=grants, items=items)


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
    return ProfileRead(**save_profile(db, name, definition, actor, revision))


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
    with db.begin() as connection, edit_configuration(connection) as state:
        require_manager(connection, str(request.user_id), request.domain)
        state["grants"][grant_id] = {
            "grant_id": grant_id,
            "domain": request.domain,
            "user_id": str(request.user_id),
            "granted_by": str(admin["sub"]),
            "granted_at": datetime.now(UTC),
            "reason": request.reason,
            "revoked_by": None,
            "revoked_at": None,
            "revocation_reason": None,
        }
    return {"grant_id": grant_id}


@router.post("/owners/grants/{grant_id}/revoke")
def revoke_owner(
    grant_id: UUID, request: RevokeRequest, db: DB, admin: SystemAdmin
) -> dict[str, str]:
    with db.begin() as connection, edit_configuration(connection) as state:
        grant = state["grants"].get(str(grant_id))
        if grant is None or grant["revoked_at"] is not None:
            reject("SILVER_OWNER_GRANT_NOT_ACTIVE")
        grant.update(
            revoked_by=str(admin["sub"]),
            revoked_at=datetime.now(UTC),
            revocation_reason=request.reason,
        )
    return {"status": "REVOKED"}


@router.get("/{profile_id}/revisions")
def profile_history(profile_id: ProfileID, db: DB, reviewer: Reviewer) -> dict[str, Any]:
    with db.begin() as connection:
        current = lock_profile(connection, profile_id)
        if not set(reviewer["roles"]) & {"ADMIN", "DATA_ADMIN"}:
            require_manager(connection, str(reviewer["sub"]), current["definition"]["domain"])
        state = configuration(connection)
        revisions = sorted(
            (r for r in state["revisions"].values() if r["profile_id"] == profile_id),
            key=lambda r: r["revision"],
        )
        approvals = [r for r in state["approvals"].values() if r["profile_id"] == profile_id]
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
    with db.begin() as connection, edit_configuration(connection) as state:
        current = state["profiles"].get(profile_id)
        if current is None:
            reject("SILVER_REGISTERED_PROFILE_REQUIRED")
        if (
            current["revision"] != request.expected_revision
            or current["fingerprint"] != request.expected_fingerprint
        ):
            reject("SILVER_PROFILE_REVISION_CONFLICT")
        if current["updated_by"] == actor:
            reject("SILVER_SELF_APPROVAL_FORBIDDEN", 403)
        require_manager(connection, actor, current["definition"]["domain"])
        key = f"{profile_id}:{request.expected_revision}"
        if key in state["approvals"]:
            reject("SILVER_REVISION_ALREADY_REVIEWED")
        grant = state["grants"].get(str(request.grant_id))
        if (
            grant is None
            or grant["revoked_at"] is not None
            or grant["user_id"] != actor
            or grant["domain"] != current["definition"]["domain"]
        ):
            reject("SILVER_DOMAIN_OWNER_REQUIRED", 403)
        state["approvals"][key] = {
            "profile_id": profile_id,
            "revision": request.expected_revision,
            "grant_id": str(request.grant_id),
            "approved_by": actor,
            "approved_at": datetime.now(UTC),
            "evidence": request.evidence,
            "allow_partial_publication": request.allow_partial_publication,
            "revoked_by": None,
            "revoked_at": None,
            "revocation_reason": None,
        }
    return {"status": "APPROVED"}


@router.post("/{profile_id}/revisions/{revision}/revoke")
def revoke_approval(
    profile_id: ProfileID, revision: int, request: RevokeRequest, db: DB, reviewer: Reviewer
) -> dict[str, str]:
    actor = str(reviewer["sub"])
    with db.begin() as connection, edit_configuration(connection) as state:
        current = state["profiles"].get(profile_id)
        if current is None:
            reject("SILVER_REGISTERED_PROFILE_REQUIRED")
        approval = state["approvals"].get(f"{profile_id}:{revision}")
        if approval is None or approval["revoked_at"] is not None:
            reject("SILVER_APPROVAL_NOT_ACTIVE")
        if "ADMIN" not in reviewer["roles"]:
            require_manager(connection, actor, current["definition"]["domain"])
            if approval["approved_by"] != actor:
                reject("SILVER_DOMAIN_OWNER_REQUIRED", 403)
        approval.update(
            revoked_by=actor, revoked_at=datetime.now(UTC), revocation_reason=request.reason
        )
    return {"status": "REVOKED"}
