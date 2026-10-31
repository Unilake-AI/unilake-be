"""Quy tắc nghiệp vụ Đào tạo (ACA-DQ-04..11) trên dữ liệu mẫu.

Các kiểm tra schema, khóa và FK chung nằm trong test_dataset_schemas.py.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise

from tests.dataset_utils import index, load_schema, rows

DS = "academic"
TODAY = date(2026, 9, 25)  # ngày chốt dữ liệu mẫu
GRADING = load_schema(DS)["grading"]


def _letter(grade: Decimal) -> str:
    return next(b["letter"] for b in GRADING["letter_bands"] if grade >= Decimal(str(b["min"])))


def test_semester_rules() -> None:  # ACA-DQ-04
    semesters = sorted(rows(DS, "semester"), key=lambda s: s["start_date"])
    for s in semesters:
        assert s["end_date"] > s["start_date"], s
        start_year = int(s["semester_code"][:4])
        assert s["academic_year"] == f"{start_year}-{start_year + 1}", s
    for prev, nxt in pairwise(semesters):
        assert prev["end_date"] < nxt["start_date"], (prev, nxt)


def test_class_capacity() -> None:  # ACA-DQ-05
    size = Counter(g["class_id"] for g in rows(DS, "enrollment_grade"))
    for c in rows(DS, "class"):
        assert size[c["class_id"]] <= int(c["max_capacity"]), c
        assert size[c["class_id"]] > 0, f"class {c['class_code']} has no student"


def test_grade_rules() -> None:
    classes = index(DS, "class")
    semesters = index(DS, "semester")
    courses = index(DS, "course")
    students = index(DS, "student")
    cohort = {k: int(v["year_label"][:4]) for k, v in index(DS, "admission_year").items()}
    w_mid = Decimal(str(GRADING["weights"]["midterm_score"]))
    w_final = Decimal(str(GRADING["weights"]["final_score"]))
    per_semester: Counter[tuple[str, str, str]] = Counter()

    for g in rows(DS, "enrollment_grade"):
        cls = classes[g["class_id"]]
        sem = semesters[cls["semester_id"]]
        scores = [g["midterm_score"], g["final_score"], g["grade_value"], g["letter_grade"]]
        if date.fromisoformat(sem["end_date"]) < TODAY:  # ACA-DQ-07
            assert all(scores), g
            expected = w_mid * Decimal(g["midterm_score"]) + w_final * Decimal(g["final_score"])
            expected = expected.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            assert Decimal(g["grade_value"]) == expected, g  # ACA-DQ-06
            assert g["letter_grade"] == _letter(expected), g
        else:
            assert not any(scores), g
        assert g["registration_date"] <= sem["end_date"], g  # ACA-DQ-08
        student = students[g["student_id"]]
        assert int(sem["semester_code"][:4]) >= cohort[student["admission_year_id"]], g  # ACA-DQ-09
        per_semester[(g["student_id"], cls["course_id"], cls["semester_id"])] += 1

    assert all(n == 1 for n in per_semester.values())  # ACA-DQ-10
    assert set(courses) >= {k[1] for k in per_semester}


def test_student_status_matches_current_registrations() -> None:  # ACA-DQ-11
    classes = index(DS, "class")
    current = {
        k
        for k, s in index(DS, "semester").items()
        if s["start_date"] <= TODAY.isoformat() <= s["end_date"]
    }
    studying = {
        g["student_id"]
        for g in rows(DS, "enrollment_grade")
        if classes[g["class_id"]]["semester_id"] in current
    }
    for s in rows(DS, "student"):
        assert (s["student_id"] in studying) == (s["student_status"] == "Active"), s
