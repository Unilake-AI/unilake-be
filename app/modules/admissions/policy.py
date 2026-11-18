"""Approved state transitions, independent of transport and database adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

RESULT_STATES = frozenset({"APPLIED", "WAITLISTED", "ACCEPTED", "REJECTED"})
ENROLLMENT_STATES = frozenset({"PENDING", "CONFIRMED", "DECLINED", "WITHDRAWN"})
RESULT_TRANSITIONS = {
    "APPLIED": {"APPLIED", "WAITLISTED", "ACCEPTED", "REJECTED"},
    "WAITLISTED": {"WAITLISTED", "ACCEPTED", "REJECTED"},
    "ACCEPTED": {"ACCEPTED"},
    "REJECTED": {"REJECTED"},
}
STAFF_ROLES = frozenset({"ADMISSIONS_STAFF", "ADMIN", "DATA_ADMIN"})
CORRECTION_ROLES = frozenset({"ADMISSIONS_MANAGER", "ADMIN"})
READ_ROLES = STAFF_ROLES | CORRECTION_ROLES


RULE_MESSAGES = {
    "ADMISSION_ACTOR_FORBIDDEN": "Tài khoản không còn quyền thực hiện nghiệp vụ tuyển sinh.",
    "ADMISSION_DATASET_FORBIDDEN": "Cần dataset Tuyển sinh đang hoạt động và thuộc đúng phạm vi được cấp quyền.",
    "ADMISSION_EXPECTED_REVISION_REQUIRED": "Cần expected_revision lấy từ bản đăng ký hiện tại trong DB.",
    "ADMISSION_REVISION_CONFLICT": "Đăng ký đã thay đổi. Hãy đọc revision mới và đối chiếu lại trước khi nộp.",
    "ADMISSION_FINAL_RESULT_LOCKED": "Kết quả đã chốt. Cần yêu cầu đính chính của Manager hoặc Admin để đổi điểm.",
    "ADMISSION_TRANSITION_NOT_ALLOWED": "Chuyển trạng thái này chưa được phép trong nghiệp vụ đã chọn.",
    "ADMISSION_ENROLLMENT_LOCKED": "Dữ liệu nhập học đã chốt. Cần yêu cầu đính chính để thay đổi.",
    "ADMISSION_ENROLLMENT_DATE_REQUIRED": "Cần ngày xác nhận nhập học do file hoặc hồ sơ cung cấp.",
    "ADMISSION_LEGACY_RECONCILIATION_REQUIRED": "Hồ sơ cũ chưa có quyết định nhập học. Manager cần tạo yêu cầu RECONCILE_LEGACY.",
    "ADMISSION_RESTORE_REQUEST_REQUIRED": "Hồ sơ đã rút cần yêu cầu RESTORE_ENROLLMENT của Manager hoặc Admin.",
    "ADMISSION_CORRECTION_FORBIDDEN": "Chỉ ADMISSIONS_MANAGER hoặc ADMIN được đính chính.",
    "ADMISSION_REGISTRATION_MISSING": "Chưa có đăng ký khớp thí sinh, chương trình, năm và phương thức.",
    "ADMISSION_APPLICANT_MAPPING_MISSING": "Chưa có ánh xạ mã thí sinh trong đúng nguồn dữ liệu.",
    "ADMISSION_SCORE_REQUIRED": "Cần điểm trước khi công bố kết quả cuối của phương thức có dùng điểm.",
    "ADMISSION_SCORE_INVALID": "Điểm phải nằm trong thang phương thức và có tối đa hai chữ số thập phân.",
    "ADMISSION_METHOD_DOES_NOT_USE_SCORE": "Phương thức không dùng điểm phải để trống trường điểm.",
    "ADMISSION_NOT_ACCEPTED": "Chỉ đăng ký trúng tuyển mới được xác nhận nhập học.",
    "ADMISSION_WITHDRAWAL_REQUIRED": "Cần giải quyết việc rút hồ sơ đã nhập học trước khi đảo kết quả xét tuyển.",
}


class AdmissionRuleError(ValueError):
    def __init__(
        self, code: str, field: str | None = None, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(code)
        self.code, self.field = code, field
        self.message = RULE_MESSAGES.get(
            code, "Không thể thực hiện thay đổi theo quy định tuyển sinh hiện tại."
        )
        self.details = details or {}


@dataclass(frozen=True)
class AdmissionActor:
    user_id: int
    roles: frozenset[str]
    dataset_id: int
    namespace: str
    request_key: str


def result_changes(
    current: dict[str, Any], patch: dict[str, Any], max_score: Decimal, *, correction: bool = False
) -> dict[str, Any]:
    if not patch or set(patch) - {"admission_score", "admission_status"}:
        raise AdmissionRuleError("ADMISSION_FIELDS_NOT_ALLOWED")
    before = str(current["admission_status"]).upper()
    after = str(patch.get("admission_status", before)).upper()
    if before not in RESULT_STATES or after not in RESULT_STATES:
        raise AdmissionRuleError("ADMISSION_STATUS_INVALID", "admission_status")
    score = patch.get("admission_score", current.get("admission_score"))
    if score is not None:
        try:
            if isinstance(score, bool):
                raise ValueError
            score = Decimal(str(score))
            if (
                not score.is_finite()
                or score != score.quantize(Decimal("0.01"))
                or not 0 <= score <= max_score
                or score >= 10000
            ):
                raise ValueError
        except (ValueError, InvalidOperation):
            raise AdmissionRuleError("ADMISSION_SCORE_INVALID", "admission_score") from None
    if max_score < 0 or (max_score == 0 and score is not None):
        raise AdmissionRuleError("ADMISSION_METHOD_DOES_NOT_USE_SCORE", "admission_score")
    if max_score > 0 and after in {"ACCEPTED", "REJECTED"} and score is None:
        raise AdmissionRuleError("ADMISSION_SCORE_REQUIRED", "admission_score")
    if not correction:
        if after not in RESULT_TRANSITIONS[before]:
            raise AdmissionRuleError("ADMISSION_TRANSITION_NOT_ALLOWED", "admission_status")
        if before in {"ACCEPTED", "REJECTED"} and score != current.get("admission_score"):
            raise AdmissionRuleError("ADMISSION_FINAL_RESULT_LOCKED", "admission_score")
    changes: dict[str, Any] = {"admission_status": after, "admission_score": score}
    enrollment = current.get("enrollment_status")
    if before == "ACCEPTED" and after != "ACCEPTED":
        if enrollment == "CONFIRMED":
            raise AdmissionRuleError("ADMISSION_WITHDRAWAL_REQUIRED", "admission_status")
        if enrollment in {"PENDING", "DECLINED"}:
            changes["enrollment_status"] = None
    if before != "ACCEPTED" and after == "ACCEPTED" and enrollment is None:
        changes["enrollment_status"] = "PENDING"
    return {
        n: v
        for n, v in changes.items()
        if (before if n == "admission_status" else current.get(n)) != v
    }


def enrollment_changes(
    current: dict[str, Any], patch: dict[str, Any], *, operation: str = "ENROLLMENT"
) -> dict[str, Any]:
    if not patch or set(patch) - {"enrollment_status", "enrollment_date"}:
        raise AdmissionRuleError("ADMISSION_FIELDS_NOT_ALLOWED")
    if str(current["admission_status"]).upper() != "ACCEPTED":
        raise AdmissionRuleError("ADMISSION_NOT_ACCEPTED", "enrollment_status")
    before = current.get("enrollment_status")
    after = patch.get("enrollment_status", before)
    if after not in ENROLLMENT_STATES:
        raise AdmissionRuleError("ADMISSION_ENROLLMENT_STATUS_INVALID", "enrollment_status")
    if before is None:
        allowed = operation == "RECONCILE_LEGACY" and after in {"PENDING", "CONFIRMED", "DECLINED"}
        if not allowed:
            raise AdmissionRuleError(
                "ADMISSION_LEGACY_RECONCILIATION_REQUIRED", "enrollment_status"
            )
    elif before == "WITHDRAWN":
        if operation != "RESTORE_ENROLLMENT" or after not in {"PENDING", "CONFIRMED"}:
            if after == before and patch.get(
                "enrollment_date", current.get("enrollment_date")
            ) == current.get("enrollment_date"):
                return {}
            raise AdmissionRuleError("ADMISSION_RESTORE_REQUEST_REQUIRED", "enrollment_status")
    elif operation in {"RECONCILE_LEGACY", "RESTORE_ENROLLMENT"}:
        raise AdmissionRuleError("ADMISSION_CORRECTION_OPERATION_INVALID", "enrollment_status")
    elif operation == "ENROLLMENT":
        if after != before and not (before == "PENDING" and after in {"CONFIRMED", "DECLINED"}):
            raise AdmissionRuleError("ADMISSION_TRANSITION_NOT_ALLOWED", "enrollment_status")
        if before in {"CONFIRMED", "DECLINED"} and patch.get(
            "enrollment_date", current.get("enrollment_date")
        ) != current.get("enrollment_date"):
            raise AdmissionRuleError("ADMISSION_ENROLLMENT_LOCKED", "enrollment_date")
    elif operation == "ENROLLMENT_CORRECTION":
        if after != before and (before, after) not in {
            ("PENDING", "CONFIRMED"),
            ("PENDING", "DECLINED"),
            ("CONFIRMED", "WITHDRAWN"),
            ("DECLINED", "CONFIRMED"),
        }:
            raise AdmissionRuleError("ADMISSION_TRANSITION_NOT_ALLOWED", "enrollment_status")
    else:
        raise AdmissionRuleError("ADMISSION_CORRECTION_OPERATION_INVALID")
    confirmation_date = patch.get("enrollment_date", current.get("enrollment_date"))
    if after == "CONFIRMED":
        if not isinstance(confirmation_date, date):
            raise AdmissionRuleError("ADMISSION_ENROLLMENT_DATE_REQUIRED", "enrollment_date")
        if before != "CONFIRMED" and patch.get("enrollment_date") is None:
            raise AdmissionRuleError("ADMISSION_ENROLLMENT_DATE_REQUIRED", "enrollment_date")
    elif "enrollment_date" in patch and confirmation_date != current.get("enrollment_date"):
        raise AdmissionRuleError("ADMISSION_ENROLLMENT_DATE_READONLY", "enrollment_date")
    changes = {"enrollment_status": after, "enrollment_date": confirmation_date}
    return {n: v for n, v in changes.items() if current.get(n) != v}
