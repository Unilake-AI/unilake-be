"""Schema và metadata ngữ nghĩa cho bản thử nghiệm mở rộng."""

from __future__ import annotations

from app.domains.base import (
    DataLayer,
    DatasetDefinition,
    FieldDefinition,
    MetricDefinition,
    SemanticModel,
)

DATASETS = (
    DatasetDefinition(
        name="demo_students",
        layer=DataLayer.SILVER,
        description="Normalized demo student records.",
        primary_key=("student_id",),
        synonyms=("learners",),
        fields=(
            FieldDefinition("student_id", "string", nullable=False, description="Student id"),
            FieldDefinition("name", "string", nullable=False, description="Student name"),
            FieldDefinition("status", "string", nullable=False, description="Enrollment status"),
        ),
    ),
    DatasetDefinition(
        name="demo_student_summary",
        layer=DataLayer.GOLD,
        description="Student counts grouped by enrollment status.",
        synonyms=("student totals",),
        fields=(
            FieldDefinition("status", "string", nullable=False),
            FieldDefinition("student_count", "integer", nullable=False),
        ),
    ),
)

SEMANTIC_MODEL = SemanticModel(
    metrics=(
        MetricDefinition(
            name="student_count",
            dataset="demo_student_summary",
            expression="SUM(student_count)",
            description="Number of students.",
            synonyms=("total students", "headcount"),
        ),
    )
)
