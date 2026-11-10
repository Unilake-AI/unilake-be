"""Các phép biến đổi Bronze → Silver → Gold cho miền demo."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.domains.base import Record, SourceMapping


class DemoTransformer:
    """Bộ biến đổi nhỏ có kết quả xác định để kiểm chứng khả năng mở rộng miền nghiệp vụ."""

    def to_silver(
        self,
        records: Sequence[Record],
        mapping: SourceMapping,
    ) -> list[Record]:
        silver_records: list[Record] = []
        for record in records:
            mapped = {
                target_field: record.get(source_field)
                for source_field, target_field in mapping.field_map.items()
            }
            mapped["status"] = "active" if mapped["status"] is True else "inactive"
            silver_records.append(mapped)
        return silver_records

    def to_gold(self, records: Sequence[Record]) -> dict[str, list[Record]]:
        counts = Counter(str(record["status"]) for record in records)
        summary: list[Record] = [
            {"status": status, "student_count": count} for status, count in sorted(counts.items())
        ]
        return {"demo_student_summary": summary}
