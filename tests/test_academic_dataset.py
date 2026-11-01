"""Academic business rules on the current curriculum/enrollment schema."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise

from tests.dataset_utils import index, load_schema, rows

DS = "academic"
TODAY = date(2026, 9, 25)
GRADING = load_schema(DS)["grading"]


def test_semester_rules() -> None:
    semesters = sorted(rows(DS, "semester"), key=lambda s: s["start_date"])
    for semester in semesters:
        assert semester["end_date"] > semester["start_date"], semester
        start_year = int(semester["semester_code"][:4])
        assert semester["academic_year"] == f"{start_year}-{start_year + 1}", semester
    for previous, following in pairwise(semesters):
        assert previous["end_date"] < following["start_date"], (previous, following)


def test_class_capacity() -> None:
    sizes = Counter(
        enrollment["class_id"]
        for enrollment in rows(DS, "enrollment")
        if enrollment["enrollment_status"] in {"Registered", "Completed"}
    )
    for cls in rows(DS, "class"):
        if cls["max_capacity"]:
            assert sizes[cls["class_id"]] <= int(cls["max_capacity"]), cls
        assert sizes[cls["class_id"]] > 0, cls


def test_grade_rules() -> None:
    classes, semesters = index(DS, "class"), index(DS, "semester")
    enrollments = index(DS, "enrollment")
    w_mid = Decimal(str(GRADING["weights"]["midterm_score"]))
    w_final = Decimal(str(GRADING["weights"]["final_score"]))
    for grade in rows(DS, "enrollment_grade"):
        enrollment = enrollments[grade["enrollment_id"]]
        semester = semesters[classes[enrollment["class_id"]]["semester_id"]]
        scores = [
            grade[field]
            for field in (
                "midterm_score",
                "final_score",
                "total_score",
                "grade_value",
                "letter_grade",
            )
        ]
        if date.fromisoformat(semester["end_date"]) < TODAY:
            assert all(scores), grade
            total = (
                w_mid * Decimal(grade["midterm_score"]) + w_final * Decimal(grade["final_score"])
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            assert Decimal(grade["total_score"]) == total, grade
            band = next(b for b in GRADING["letter_bands"] if total >= Decimal(str(b["min"])))
            assert grade["letter_grade"] == band["letter"], grade
            assert Decimal(grade["grade_value"]) == Decimal(str(band["grade_value"])), grade
        else:
            assert not any(scores), grade
        if grade["registration_date"]:
            assert grade["registration_date"] == enrollment["enrollment_date"], grade


def test_enrollment_dates_and_course_uniqueness() -> None:
    classes, semesters = index(DS, "class"), index(DS, "semester")
    students, years = index(DS, "student"), index(DS, "admission_year")
    per_semester: Counter[tuple[str, str, str]] = Counter()
    for enrollment in rows(DS, "enrollment"):
        cls = classes[enrollment["class_id"]]
        semester = semesters[cls["semester_id"]]
        assert enrollment["enrollment_date"] <= semester["end_date"], enrollment
        student = students[enrollment["student_id"]]
        assert int(semester["semester_code"][:4]) >= int(
            years[student["admission_year_id"]]["year_label"][:4]
        ), enrollment
        per_semester[(student["student_id"], cls["course_id"], cls["semester_id"])] += 1
    assert all(count == 1 for count in per_semester.values())


def test_student_status_matches_current_registrations() -> None:
    classes = index(DS, "class")
    current = {
        key
        for key, semester in index(DS, "semester").items()
        if semester["start_date"] <= TODAY.isoformat() <= semester["end_date"]
    }
    studying = {
        e["student_id"]
        for e in rows(DS, "enrollment")
        if e["enrollment_status"] in {"Registered", "Completed"}
        and classes[e["class_id"]]["semester_id"] in current
    }
    for student in rows(DS, "student"):
        assert (student["student_id"] in studying) == (student["student_status"] == "Active"), (
            student
        )


def test_curricula_and_prerequisites() -> None:
    active: Counter[str] = Counter()
    members: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for curriculum in rows(DS, "curriculum"):
        if curriculum["effective_from"] and curriculum["effective_to"]:
            assert curriculum["effective_from"] < curriculum["effective_to"], curriculum
        if curriculum["status"] == "Active":
            active[curriculum["program_id"]] += 1
    assert all(count == 1 for count in active.values())
    for member in rows(DS, "curriculum_course"):
        members[member["curriculum_id"]][member["course_id"]] = member
        if member["course_type"] == "Elective":
            assert member["is_required"] == "false", member
    for courses in members.values():
        for course in courses.values():
            prerequisite = course["prerequisite_course_id"]
            if prerequisite:
                assert prerequisite != course["course_id"], course
                assert prerequisite in courses, course
                earlier = courses[prerequisite]
                if course["semester_no"] and earlier["semester_no"]:
                    assert int(earlier["semester_no"]) < int(course["semester_no"]), course


def test_class_lecturer_assignments() -> None:
    classes, semesters = index(DS, "class"), index(DS, "semester")
    primary: Counter[str] = Counter()
    for assignment in rows(DS, "class_lecturer"):
        semester = semesters[classes[assignment["class_id"]]["semester_id"]]
        if assignment["is_primary"] == "true":
            primary[assignment["class_id"]] += 1
        for field in ("assigned_from", "assigned_to"):
            if assignment[field]:
                assert semester["start_date"] <= assignment[field] <= semester["end_date"], (
                    assignment
                )
        if assignment["assigned_from"] and assignment["assigned_to"]:
            assert assignment["assigned_from"] <= assignment["assigned_to"], assignment
    assert set(primary) == set(classes)
    assert all(count == 1 for count in primary.values())
