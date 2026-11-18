from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.modules.admissions.contracts import CorrectionCreate
from app.modules.admissions.policy import AdmissionRuleError, enrollment_changes, result_changes


def record(**patch):
    return {
        "admission_id": 1,
        "revision": 1,
        "admission_status": "APPLIED",
        "admission_score": Decimal("20"),
        "enrollment_status": None,
        "enrollment_date": None,
        **patch,
    }


@pytest.mark.parametrize("before", ["APPLIED", "WAITLISTED", "ACCEPTED", "REJECTED"])
@pytest.mark.parametrize("after", ["APPLIED", "WAITLISTED", "ACCEPTED", "REJECTED"])
def test_staff_result_transition_matrix(before, after):
    permitted = {
        "APPLIED": {"APPLIED", "WAITLISTED", "ACCEPTED", "REJECTED"},
        "WAITLISTED": {"WAITLISTED", "ACCEPTED", "REJECTED"},
        "ACCEPTED": {"ACCEPTED"},
        "REJECTED": {"REJECTED"},
    }
    current = record(admission_status=before)
    if after in permitted[before]:
        patch = result_changes(current, {"admission_status": after}, Decimal(30))
        assert patch.get("admission_status", before) == after
        if before != "ACCEPTED" and after == "ACCEPTED":
            assert patch["enrollment_status"] == "PENDING"
    else:
        with pytest.raises(AdmissionRuleError, match="ADMISSION_TRANSITION_NOT_ALLOWED"):
            result_changes(current, {"admission_status": after}, Decimal(30))


@pytest.mark.parametrize(
    "score",
    [
        Decimal("-0.01"),
        Decimal("30.01"),
        Decimal("1.001"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("10000"),
    ],
)
def test_scores_require_method_range_and_two_decimal_places(score):
    with pytest.raises(AdmissionRuleError, match="ADMISSION_SCORE_INVALID"):
        result_changes(record(), {"admission_score": score}, Decimal(30))


def test_no_score_method_and_final_results():
    current = record(admission_score=None)
    assert (
        result_changes(current, {"admission_status": "ACCEPTED"}, Decimal(0))["enrollment_status"]
        == "PENDING"
    )
    with pytest.raises(AdmissionRuleError, match="ADMISSION_SCORE_REQUIRED"):
        result_changes(current, {"admission_status": "ACCEPTED"}, Decimal(30))
    with pytest.raises(AdmissionRuleError, match="ADMISSION_METHOD_DOES_NOT_USE_SCORE"):
        result_changes(current, {"admission_score": Decimal(0)}, Decimal(0))


def test_only_explicit_correction_can_change_final_score():
    current = record(admission_status="ACCEPTED", enrollment_status="PENDING")
    with pytest.raises(AdmissionRuleError, match="ADMISSION_FINAL_RESULT_LOCKED"):
        result_changes(current, {"admission_score": Decimal(21)}, Decimal(30))
    assert result_changes(
        current, {"admission_score": Decimal(21)}, Decimal(30), correction=True
    ) == {"admission_score": Decimal(21)}
    assert (
        result_changes(
            current, {"admission_score": Decimal(20), "admission_status": "ACCEPTED"}, Decimal(30)
        )
        == {}
    )


def test_confirmed_registration_must_be_withdrawn_before_result_reversal():
    current = record(
        admission_status="ACCEPTED",
        enrollment_status="CONFIRMED",
        enrollment_date=date(2026, 11, 1),
    )
    with pytest.raises(AdmissionRuleError, match="ADMISSION_WITHDRAWAL_REQUIRED"):
        result_changes(current, {"admission_status": "REJECTED"}, Decimal(30), correction=True)


def test_staff_confirmation_requires_acceptance_and_a_file_date():
    with pytest.raises(AdmissionRuleError, match="ADMISSION_NOT_ACCEPTED"):
        enrollment_changes(
            record(), {"enrollment_status": "CONFIRMED", "enrollment_date": date(2026, 11, 1)}
        )
    current = record(admission_status="ACCEPTED", enrollment_status="PENDING")
    with pytest.raises(AdmissionRuleError, match="ADMISSION_ENROLLMENT_DATE_REQUIRED"):
        enrollment_changes(current, {"enrollment_status": "CONFIRMED"})
    patch = {"enrollment_status": "CONFIRMED", "enrollment_date": date(2026, 11, 1)}
    assert enrollment_changes(current, patch) == patch
    assert enrollment_changes(current, {"enrollment_status": "DECLINED"}) == {
        "enrollment_status": "DECLINED"
    }


def test_legacy_unknown_requires_separate_reconciliation():
    current = record(admission_status="ACCEPTED")
    for operation in ("ENROLLMENT", "ENROLLMENT_CORRECTION"):
        with pytest.raises(AdmissionRuleError, match="ADMISSION_LEGACY_RECONCILIATION_REQUIRED"):
            enrollment_changes(current, {"enrollment_status": "PENDING"}, operation=operation)
    assert enrollment_changes(
        current, {"enrollment_status": "PENDING"}, operation="RECONCILE_LEGACY"
    ) == {"enrollment_status": "PENDING"}


def test_withdrawal_preserves_date_and_restore_is_explicit():
    current = record(
        admission_status="ACCEPTED",
        enrollment_status="CONFIRMED",
        enrollment_date=date(2026, 11, 1),
    )
    with pytest.raises(AdmissionRuleError, match="ADMISSION_TRANSITION_NOT_ALLOWED"):
        enrollment_changes(current, {"enrollment_status": "WITHDRAWN"})
    assert enrollment_changes(
        current, {"enrollment_status": "WITHDRAWN"}, operation="ENROLLMENT_CORRECTION"
    ) == {"enrollment_status": "WITHDRAWN"}
    current["enrollment_status"] = "WITHDRAWN"
    with pytest.raises(AdmissionRuleError, match="ADMISSION_RESTORE_REQUEST_REQUIRED"):
        enrollment_changes(
            current,
            {"enrollment_status": "CONFIRMED", "enrollment_date": date(2026, 11, 2)},
            operation="ENROLLMENT_CORRECTION",
        )
    assert enrollment_changes(
        current,
        {"enrollment_status": "CONFIRMED", "enrollment_date": date(2026, 11, 2)},
        operation="RESTORE_ENROLLMENT",
    )["enrollment_date"] == date(2026, 11, 2)


def test_correction_rejects_unapproved_fields_blank_reason_and_fake_revision():
    base = {
        "dataset_id": 31,
        "admission_id": 1,
        "expected_revision": 1,
        "operation": "RESULT_CORRECTION",
        "reason": "Điều chỉnh theo biên bản",
        "changes": {"admission_score": "25.50"},
    }
    assert CorrectionCreate.model_validate(base).changes == {"admission_score": "25.50"}
    for patch in (
        {"reason": " "},
        {"expected_revision": True},
        {"changes": {"program_id": 8}},
        {"changes": {}},
        {"changes": {"admission_score": "1.001"}},
    ):
        with pytest.raises(ValidationError):
            CorrectionCreate.model_validate({**base, **patch})
