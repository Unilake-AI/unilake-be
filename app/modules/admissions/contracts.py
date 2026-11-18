from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.admissions.policy import AdmissionRuleError as AdmissionRuleError

ResultState = Literal["APPLIED", "WAITLISTED", "ACCEPTED", "REJECTED"]
EnrollmentState = Literal["PENDING", "CONFIRMED", "DECLINED", "WITHDRAWN"]
CorrectionOperation = Literal[
    "RESULT_CORRECTION", "ENROLLMENT_CORRECTION", "RESTORE_ENROLLMENT", "RECONCILE_LEGACY"
]


class ResultPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    admission_score: Decimal | None = Field(default=None, max_digits=6, decimal_places=2)
    admission_status: ResultState | None = None

    @model_validator(mode="after")
    def valid_patch(self) -> ResultPatch:
        if not self.model_fields_set or (
            "admission_status" in self.model_fields_set and self.admission_status is None
        ):
            raise ValueError("Cần trường kết quả hợp lệ để sửa")
        return self


class EnrollmentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enrollment_status: EnrollmentState
    enrollment_date: date | None = None


class CorrectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    correction_id: UUID = Field(default_factory=uuid4)
    dataset_id: int = Field(gt=0)
    admission_id: int = Field(gt=0)
    expected_revision: int = Field(strict=True, gt=0, lt=2**63)
    operation: CorrectionOperation
    reason: str = Field(min_length=1, max_length=2000)
    changes: dict[str, Any]

    @model_validator(mode="after")
    def valid_changes(self) -> CorrectionCreate:
        patch = (
            ResultPatch.model_validate(self.changes)
            if self.operation == "RESULT_CORRECTION"
            else EnrollmentPatch.model_validate(self.changes)
        )
        self.changes = patch.model_dump(mode="json", exclude_unset=True)
        return self
