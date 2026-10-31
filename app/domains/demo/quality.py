"""Các quy tắc chất lượng dữ liệu thuộc miền demo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domains.base import QualityResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.domains.base import QualityRule, Record


@dataclass(frozen=True)
class RequiredFieldsRule:
    """Yêu cầu các trường được chọn phải có giá trị không rỗng."""

    rule_id: str
    dataset: str
    description: str
    fields: tuple[str, ...]

    def evaluate(self, records: Sequence[Record]) -> QualityResult:
        failed_count = sum(
            1 for record in records if any(record.get(field) in (None, "") for field in self.fields)
        )
        return QualityResult(
            rule_id=self.rule_id,
            passed=failed_count == 0,
            message="Required fields are populated"
            if failed_count == 0
            else "Missing required fields",
            failed_count=failed_count,
        )


REQUIRED_FIELDS_RULE: QualityRule = RequiredFieldsRule(
    rule_id="demo_students_required_fields",
    dataset="demo_students",
    description="Student id and name are required.",
    fields=("student_id", "name"),
)

QUALITY_RULES: tuple[QualityRule, ...] = (REQUIRED_FIELDS_RULE,)
