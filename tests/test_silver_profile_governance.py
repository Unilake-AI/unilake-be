"""Lifecycle and production admission tests use real SQL, synthetic owners only."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.core.exceptions import AppException
from app.modules.ingestion.contracts import FileProvenance
from app.modules.pipeline.models.profiles import SilverDomainOwner, SilverProfileRevision
from app.modules.pipeline.repositories.silver import SilverRepository
from app.modules.pipeline.repositories.silver_storage import LocalSilverStore, SilverError
from app.modules.pipeline.routers import snapshots as snapshots_router
from app.modules.pipeline.services.source_profiles import SourceProfile
from app.modules.pipeline.silver.publication import SilverPublicationService
from app.repositories.silver_governance_repo import approval_required, approved_evidence
from tests.test_silver_management_api import create_profile, environment  # noqa: F401
from tests.test_silver_publication import normalized
from tests.test_source_cleaning import definition


@pytest.fixture
def governance(environment):  # noqa: F811 - imported pytest fixture
    client, engine, user = environment
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE app_user (app_user_id INTEGER PRIMARY KEY, is_active BOOLEAN)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE role (role_id INTEGER PRIMARY KEY, role_code TEXT)"
        )
        connection.exec_driver_sql("CREATE TABLE user_role (app_user_id INTEGER, role_id INTEGER)")
        connection.exec_driver_sql("INSERT INTO app_user VALUES (1, 1), (2, 1), (3, 1)")
        connection.exec_driver_sql(
            "INSERT INTO role VALUES (1, 'HR_MANAGER'), (2, 'ACADEMIC_MANAGER')"
        )
        connection.exec_driver_sql("INSERT INTO user_role VALUES (1, 1), (2, 1), (3, 2)")
    created = create_profile(client)
    user.update(sub="1", roles=["ADMIN"])
    response = client.post(
        "/pipeline/profiles/owners/grants",
        json={
            "domain": "hr",
            "user_id": 2,
            "reason": "Synthetic delegation for governance test",
        },
    )
    assert response.status_code == 201, response.text
    review = {
        "expected_revision": created["revision"],
        "expected_fingerprint": created["fingerprint"],
        "grant_id": response.json()["grant_id"],
        "evidence": "Synthetic owner decision, not production approval",
        "allow_partial_publication": False,
    }
    user.update(sub="2", roles=["HR_MANAGER"])
    return client, engine, user, created, review


def approve(governance):
    client, _, _, _, review = governance
    response = client.post("/pipeline/profiles/positions/approve", json=review)
    assert response.status_code == 201, response.text


def test_unapproved_rejected_and_revision_retained(governance):
    client, engine, user, created, _ = governance
    with engine.begin() as connection, pytest.raises(AppException) as exc:
        approved_evidence(connection, "positions", created["fingerprint"])
    assert exc.value.code == "SILVER_PROFILE_APPROVAL_REQUIRED"
    approve(governance)
    with engine.begin() as connection:
        proof = approved_evidence(connection, "positions", created["fingerprint"])
    assert proof["approved_by"] == "2"
    user.update(sub="1", roles=["DATA_ADMIN"])
    changed = definition()
    changed["collapse_exact_duplicates"] = True
    response = client.put(
        "/pipeline/profiles/positions", json={"expected_revision": 1, "definition": changed}
    )
    assert response.status_code == 200
    with engine.begin() as connection:
        revisions = (
            connection.execute(
                select(SilverProfileRevision).order_by(SilverProfileRevision.revision)
            )
            .mappings()
            .all()
        )
        assert len(revisions) == 2
        assert revisions[0]["fingerprint"] == created["fingerprint"]
        with pytest.raises(AppException) as exc:
            approved_evidence(connection, "positions", response.json()["fingerprint"])
        assert exc.value.code == "SILVER_PROFILE_APPROVAL_REQUIRED"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("stale", "SILVER_PROFILE_REVISION_CONFLICT"),
        ("self", "SILVER_SELF_APPROVAL_FORBIDDEN"),
        ("domain", "SILVER_DOMAIN_OWNER_REQUIRED"),
        ("grant", "SILVER_DOMAIN_OWNER_REQUIRED"),
    ],
)
def test_review_requires_exact_revision_independent_owner_and_delegation(
    governance, mutation, code
):
    client, _, user, _, review = governance
    if mutation == "stale":
        review["expected_revision"] = 2
    elif mutation == "self":
        user.update(sub="1", roles=["HR_MANAGER"])
    elif mutation == "domain":
        user.update(sub="3", roles=["ACADEMIC_MANAGER"])
    else:
        review["grant_id"] = "00000000-0000-0000-0000-000000000000"
    response = client.post("/pipeline/profiles/positions/approve", json=review)
    assert response.status_code in {403, 409}, response.text
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize("mutation", ["approval", "grant", "inactive", "role", "definition"])
def test_final_check_rejects_revoked_or_changed_authority(governance, mutation):
    client, engine, user, created, review = governance
    approve(governance)
    with engine.begin() as connection:
        proof = approved_evidence(connection, "positions", created["fingerprint"])
    if mutation == "approval":
        assert (
            client.post(
                "/pipeline/profiles/positions/revisions/1/revoke",
                json={"reason": "Owner withdraws synthetic review"},
            ).status_code
            == 200
        )
    elif mutation == "grant":
        user.update(sub="1", roles=["ADMIN"])
        assert (
            client.post(
                f"/pipeline/profiles/owners/grants/{review['grant_id']}/revoke",
                json={"reason": "Delegation ended for this test"},
            ).status_code
            == 200
        )
    else:
        with engine.begin() as connection:
            if mutation == "inactive":
                connection.exec_driver_sql("UPDATE app_user SET is_active=0 WHERE app_user_id=2")
            elif mutation == "role":
                connection.exec_driver_sql("DELETE FROM user_role WHERE app_user_id=2")
            else:
                connection.execute(update(SilverProfileRevision).values(fingerprint="0" * 64))
    with engine.begin() as connection, pytest.raises(AppException):
        approved_evidence(connection, "positions", created["fingerprint"], expected=proof)


def test_partial_requires_explicit_owner_decision(governance):
    _, engine, _, created, _ = governance
    approve(governance)
    with engine.begin() as connection, pytest.raises(AppException) as exc:
        approved_evidence(connection, "positions", created["fingerprint"], partial=True)
    assert exc.value.code == "SILVER_PARTIAL_PUBLICATION_NOT_APPROVED"


def test_revoked_revision_cannot_be_reapproved(governance):
    client, _, _, _, review = governance
    approve(governance)
    assert (
        client.post(
            "/pipeline/profiles/positions/revisions/1/revoke",
            json={"reason": "Withdraw and require a new review"},
        ).status_code
        == 200
    )
    assert client.post("/pipeline/profiles/positions/approve", json=review).status_code == 409


@pytest.mark.parametrize(
    ("env", "required"),
    [
        ("production", True),
        ("staging", True),
        ("prod", True),
        ("unknown", True),
        ("development", False),
        ("test", False),
    ],
)
def test_production_cannot_disable_approval(monkeypatch, env, required):
    monkeypatch.setattr(settings, "app_env", env)
    assert approval_required() is required


def test_direct_repository_publish_has_no_production_bypass(governance, monkeypatch):
    _, engine, _, _, _ = governance
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(SilverError, match="SILVER_PROFILE_APPROVAL_REQUIRED"):
        SilverRepository(engine).publish("not-a-run")


def test_staff_cannot_delegate_or_review(governance):
    client, _, user, _, review = governance
    user.update(sub="2", roles=["HR_STAFF"])
    assert client.post("/pipeline/profiles/positions/approve", json=review).status_code == 403
    assert (
        client.post(
            "/pipeline/profiles/owners/grants",
            json={"domain": "hr", "user_id": 2, "reason": "Unauthorized delegation attempt"},
        ).status_code
        == 403
    )


def test_owner_grant_audit_survives_revocation(governance):
    client, engine, user, _, review = governance
    user.update(sub="1", roles=["ADMIN"])
    assert (
        client.post(
            f"/pipeline/profiles/owners/grants/{review['grant_id']}/revoke",
            json={"reason": "Retain original delegation evidence"},
        ).status_code
        == 200
    )
    with engine.connect() as connection:
        grant = connection.execute(select(SilverDomainOwner)).mappings().one()
        assert grant["reason"] == "Synthetic delegation for governance test"
        assert grant["revoked_by"] == "1"


@pytest.fixture
def production(governance, monkeypatch, tmp_path, request):
    if getattr(request, "param", None) == "partial":
        governance[4]["allow_partial_publication"] = True
    approve(governance)
    _, engine, _, created, _ = governance
    with engine.begin() as connection:
        proof = approved_evidence(connection, "positions", created["fingerprint"])
    profile = SourceProfile.model_validate(definition())
    publisher = SilverPublicationService(
        repository=SilverRepository(engine),
        storage=LocalSilverStore(tmp_path / "objects"),
        contracts=profile.contracts,
        scope=profile.scope,
        profile_approvals=[proof],
    )
    provenance = FileProvenance(
        "17",
        "21",
        "positions.csv",
        "CSV",
        "s3://bronze/input.csv",
        "v1",
        "0" * 64,
        "1" * 64,
        "2" * 64,
        "2026-01-01T00:00:00Z",
        "parsed_record_ordinal",
    )
    result = replace(
        normalized(),
        key=profile.key(),
        provenance=provenance,
        input_version=provenance.input_version,
    )
    monkeypatch.setattr(settings, "app_env", "production")
    return publisher, result


def test_production_publish_reuse_and_manifest_evidence(production):
    publisher, result = production
    first = publisher.publish((result,))
    assert publisher.publish((result,)).reused
    manifest = publisher.describe_snapshot(first.snapshot_id)
    assert manifest["governance"]["profile_approvals"][0]["status"] == "APPROVED"


@pytest.mark.parametrize("operation", ["publish", "reuse", "resume", "activate"])
def test_revocation_blocks_every_publication_path(governance, production, monkeypatch, operation):
    client, _, user, _, _ = governance
    publisher, result = production
    receipt = publisher.publish((result,))
    assert (
        client.post(
            "/pipeline/profiles/positions/revisions/1/revoke",
            json={"reason": "Withdraw approval before publication"},
        ).status_code
        == 200
    )
    if operation == "activate":
        user.update(sub="1", roles=["DATA_ADMIN"])
        monkeypatch.setattr(snapshots_router, "publisher_for", lambda *_: publisher)
        response = client.post(
            f"/pipeline/silver/snapshots/{receipt.snapshot_id}/activate",
            params={"profile_id": "positions"},
            json={"expected_current_snapshot": receipt.snapshot_id},
        )
        assert response.status_code == 409
    else:
        with pytest.raises(AppException) as exc:  # noqa: PT012 - exercise each public path
            if operation == "resume":
                publisher.resume(receipt.snapshot_id)
            elif operation == "reuse":
                publisher.publish((result,))
            else:
                publisher.publish((result,), base_snapshot=receipt.snapshot_id)
        assert exc.value.code == "SILVER_PROFILE_APPROVAL_REQUIRED"
    assert publisher.repository.head(publisher.scope) == receipt.snapshot_id


def test_approved_profile_cannot_authorize_different_mapping(production):
    publisher, result = production
    changed = replace(result, key=replace(result.key, mapping_version="unreviewed"))
    with pytest.raises(AppException) as exc:
        publisher.publish((changed,))
    assert exc.value.code == "SILVER_APPROVED_LINEAGE_REQUIRED"
    assert publisher.repository.head(publisher.scope) is None


def test_partial_publication_is_blocked_at_commit_without_approval(production):
    publisher, result = production
    report = publisher.storage.put("synthetic-quarantine.json", b'{"synthetic": true}')
    with pytest.raises(AppException) as exc:
        publisher.publish((result,), quarantine_report=report)
    assert exc.value.code == "SILVER_PARTIAL_PUBLICATION_NOT_APPROVED"
    assert publisher.repository.head(publisher.scope) is None


def test_owner_can_explicitly_approve_partial_and_status_is_visible(governance):
    client, engine, user, created, review = governance
    review["allow_partial_publication"] = True
    approve(governance)
    with engine.begin() as connection:
        proof = approved_evidence(connection, "positions", created["fingerprint"], partial=True)
    assert proof["allow_partial_publication"] is True
    user.update(sub="1", roles=["DATA_ADMIN"])
    assert client.get("/pipeline/profiles/positions").json()["approval_status"] == "APPROVED"
    user.update(sub="2", roles=["HR_MANAGER"])
    assert (
        client.post(
            "/pipeline/profiles/positions/revisions/1/revoke",
            json={"reason": "Withdraw synthetic review for test"},
        ).status_code
        == 200
    )
    user.update(sub="1", roles=["DATA_ADMIN"])
    assert (
        client.get("/pipeline/profiles/positions").json()["approval_status"]
        == "NEEDS_DOMAIN_OWNER_APPROVAL"
    )


@pytest.mark.parametrize("production", ["partial"], indirect=True)
@pytest.mark.parametrize("evidence", ["valid", "missing", "blocked", "wrong_rule"])
def test_partial_requires_key_safety_evidence(production, evidence):
    publisher, result = production
    check = {
        "version": "partial-key-safety-v1",
        "rule_version": result.key.mapping_version,
        "status": "PASSED",
        "blockers": [],
    }
    if evidence == "blocked":
        check["status"] = "BLOCKED"
    elif evidence == "wrong_rule":
        check["rule_version"] = "unreviewed-rule"
    report = {} if evidence == "missing" else {"partial_key_check": check}
    ref = publisher.storage.put("quarantine-evidence.json", json.dumps(report).encode())
    if evidence == "valid":
        receipt = publisher.publish((result,), quarantine_report=ref)
        assert publisher.describe_snapshot(receipt.snapshot_id)["partial_key_check"] == check
    else:
        with pytest.raises(AppException) as exc:
            publisher.publish((result,), quarantine_report=ref)
        assert exc.value.code == "SILVER_PARTIAL_KEY_CHECK_REQUIRED"
        assert publisher.repository.head(publisher.scope) is None


def test_reuse_records_cleaning_in_same_authorized_transaction(production):
    publisher, result = production
    commits = []
    publisher.repository.on_published = lambda connection, snapshot: commits.append(
        (connection.in_transaction(), snapshot)
    )
    first = publisher.publish((result,))
    assert publisher.publish((result,)).reused
    assert commits == [(True, first.snapshot_id), (True, first.snapshot_id)]
