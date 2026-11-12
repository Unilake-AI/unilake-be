"""Production admission checks and durable owner approval evidence.

Lock order: profile -> approval -> owner grant. Publication holds these locks
until its head transaction commits, so edit/revoke cannot race the final check.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC
from typing import Any, NoReturn

from sqlalchemy import select
from sqlalchemy.engine import Connection

from app.core.config import settings
from app.core.exceptions import AppException
from app.modules.auth.models import Role, UserRole
from app.modules.pipeline.contracts.identity import digest
from app.modules.pipeline.models.profiles import (
    SilverDomainOwner,
    SilverProfileApproval,
    SilverProfileRevision,
    SilverSourceProfile,
)
from app.modules.pipeline.services.source_profiles import SourceProfile
from app.modules.users.models import User


def approval_required() -> bool:
    # Unknown environments fail closed as well. No production bypass switch.
    return settings.app_env.strip().lower() not in {"development", "test", "local"}


def reject(code: str, status: int = 409) -> NoReturn:
    raise AppException(
        "Cấu hình chưa đủ điều kiện phê duyệt/công bố", code=code, status_code=status
    )


def require_manager(connection: Connection, user_id: str, domain: str) -> None:
    role = {
        "admissions": "ADMISSIONS_MANAGER",
        "academic": "ACADEMIC_MANAGER",
        "hr": "HR_MANAGER",
    }.get(domain)
    if not role or not user_id.isdecimal():
        reject("SILVER_DOMAIN_OWNER_REQUIRED", 403)
    found = connection.execute(
        select(User.app_user_id)
        .join(UserRole, UserRole.app_user_id == User.app_user_id)
        .join(Role, Role.role_id == UserRole.role_id)
        .where(User.app_user_id == int(user_id), User.is_active.is_(True), Role.role_code == role)
        .with_for_update(read=True)
    ).first()
    if found is None:
        reject("SILVER_DOMAIN_OWNER_REQUIRED", 403)


def lock_profile(connection: Connection, profile_id: str) -> dict[str, Any]:
    row = (
        connection.execute(
            select(SilverSourceProfile)
            .where(SilverSourceProfile.profile_id == profile_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        reject("SILVER_REGISTERED_PROFILE_REQUIRED")
    return dict(row)


def approved_evidence(
    connection: Connection,
    profile_id: str,
    fingerprint: str,
    *,
    partial: bool = False,
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = lock_profile(connection, profile_id)
    if current["fingerprint"] != fingerprint:
        reject("SILVER_PROFILE_REVISION_CONFLICT")
    revision = (
        connection.execute(
            select(SilverProfileRevision).where(
                SilverProfileRevision.profile_id == profile_id,
                SilverProfileRevision.revision == current["revision"],
            )
        )
        .mappings()
        .first()
    )
    if revision is None or revision["fingerprint"] != fingerprint:
        reject("SILVER_PROFILE_HISTORY_REQUIRED")
    profile = SourceProfile.model_validate(revision["definition"])
    if profile.fingerprint != fingerprint or current["definition"] != revision["definition"]:
        reject("SILVER_PROFILE_INTEGRITY_ERROR")
    approval = (
        connection.execute(
            select(SilverProfileApproval)
            .where(
                SilverProfileApproval.profile_id == profile_id,
                SilverProfileApproval.revision == current["revision"],
            )
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if approval is None or approval["revoked_at"] is not None:
        reject("SILVER_PROFILE_APPROVAL_REQUIRED")
    grant = (
        connection.execute(
            select(SilverDomainOwner)
            .where(
                SilverDomainOwner.grant_id == approval["grant_id"],
            )
            .with_for_update()
        )
        .mappings()
        .one()
    )
    if (
        grant["revoked_at"] is not None
        or grant["domain"] != profile.domain
        or grant["user_id"] != approval["approved_by"]
        or approval["approved_by"] == revision["created_by"]
    ):
        reject("SILVER_DOMAIN_OWNER_REQUIRED", 403)
    require_manager(connection, approval["approved_by"], profile.domain)
    if partial and not approval["allow_partial_publication"]:
        reject("SILVER_PARTIAL_PUBLICATION_NOT_APPROVED")
    evidence = {
        "status": "APPROVED",
        "profile_id": profile_id,
        "revision": current["revision"],
        "fingerprint": fingerprint,
        "rule_version": profile.rule_version,
        "grant_id": grant["grant_id"],
        "approved_by": approval["approved_by"],
        "approved_at": (
            approval["approved_at"].replace(tzinfo=UTC)
            if approval["approved_at"].tzinfo is None
            else approval["approved_at"].astimezone(UTC)
        ).isoformat(),
        "evidence": approval["evidence"],
        "allow_partial_publication": approval["allow_partial_publication"],
        "mapping_key": asdict(profile.key()),
        "scope": f"production:{profile.scope}",
        "schema_fingerprint": digest(profile.contracts),
    }
    if expected is not None and evidence != expected:
        reject("SILVER_APPROVAL_EVIDENCE_CHANGED")
    return evidence


def authorize_manifest(connection: Connection, manifest: dict[str, Any]) -> None:
    proofs = manifest.get("governance", {}).get("profile_approvals")
    if not isinstance(proofs, list) or not proofs:
        reject("SILVER_SNAPSHOT_APPROVAL_REQUIRED")
    recheck_approvals(connection, proofs)
    for proof in sorted(proofs, key=lambda item: item["profile_id"]):
        if (
            proof["scope"] != manifest["scope"]
            or proof["schema_fingerprint"] != manifest["schema_fingerprint"]
        ):
            reject("SILVER_APPROVAL_SCOPE_MISMATCH")
    # Every contributing batch must match a reviewed mapping, including retained parent rows.
    for artifact in manifest["entities"].values():
        if artifact["rows"] and not artifact.get("lineage"):
            reject("SILVER_APPROVED_LINEAGE_REQUIRED")
        for batch in artifact.get("lineage", []):
            if not any(proof["mapping_key"] == batch["key"] for proof in proofs):
                reject("SILVER_APPROVED_LINEAGE_REQUIRED")
    if manifest.get("quarantine_report"):
        for batches in manifest["updated_lineage"].values():
            for batch in batches:
                if not any(
                    proof["mapping_key"] == batch["key"] and proof["allow_partial_publication"]
                    for proof in proofs
                ):
                    reject("SILVER_PARTIAL_PUBLICATION_NOT_APPROVED")
        check = manifest.get("partial_key_check")
        if (
            not isinstance(check, dict)
            or check.get("version") != "partial-key-safety-v1"
            or check.get("status") != "PASSED"
            or check.get("blockers") != []
            or not any(proof["rule_version"] == check.get("rule_version") for proof in proofs)
        ):
            reject("SILVER_PARTIAL_KEY_CHECK_REQUIRED")


def recheck_approvals(connection: Connection, proofs: list[dict[str, Any]]) -> None:
    ordered = sorted(proofs, key=lambda item: item["profile_id"])
    # Lock ALL profiles before touching a shared owner grant; two profiles can
    # have the same owner and must not invert profile/grant lock order.
    for proof in ordered:
        lock_profile(connection, proof["profile_id"])
    for proof in ordered:
        connection.execute(
            select(SilverProfileApproval)
            .where(
                SilverProfileApproval.profile_id == proof["profile_id"],
                SilverProfileApproval.revision == proof["revision"],
            )
            .with_for_update()
        ).all()
    for grant_id in sorted({proof["grant_id"] for proof in ordered}):
        connection.execute(
            select(SilverDomainOwner)
            .where(
                SilverDomainOwner.grant_id == grant_id,
            )
            .with_for_update()
        ).all()
    for proof in ordered:
        approved_evidence(connection, proof["profile_id"], proof["fingerprint"], expected=proof)
