"""Quy tắc nghiệp vụ Tuyển sinh (ADM-DQ-04..12) trên dữ liệu mẫu.

Các kiểm tra schema, khóa và FK chung nằm trong test_dataset_schemas.py.
"""

from __future__ import annotations

from collections import Counter

from tests.dataset_utils import index, rows

DS = "admissions"


def test_admission_rules() -> None:
    years = index(DS, "admission_year")
    methods = {k: v["method_code"] for k, v in index(DS, "admission_method").items()}
    applicants = index(DS, "applicant")
    first_year: dict[str, int] = {}
    accepted: Counter[tuple[str, str]] = Counter()

    for y in years.values():  # ADM-DQ-04
        assert y["end_date"] > y["start_date"], y

    for a in rows(DS, "admission"):
        y = years[a["admission_year_id"]]
        assert y["start_date"] <= a["application_date"] <= y["end_date"], a  # ADM-DQ-05
        if methods[a["admission_method_id"]] == "XTT":  # ADM-DQ-06
            assert a["admission_score"] == "", a
        elif a["admission_status"] != "Applied":
            assert a["admission_score"], a
        if a["admission_status"] == "Accepted":
            accepted[(a["applicant_id"], a["admission_year_id"])] += 1
        start_year = int(y["year_label"][:4])
        first_year[a["applicant_id"]] = min(start_year, first_year.get(a["applicant_id"], 9999))

    assert all(n == 1 for n in accepted.values())  # ADM-DQ-07
    assert set(first_year) == set(applicants), "every applicant has at least one admission"
    for applicant_id, start_year in first_year.items():  # ADM-DQ-08
        age = start_year - int(applicants[applicant_id]["date_of_birth"][:4])
        assert 16 <= age <= 30, applicant_id


def test_enrollment_and_student_rules() -> None:
    admissions = index(DS, "admission")
    years = index(DS, "admission_year")
    students = index(DS, "student")
    applicants = index(DS, "applicant")

    for e in rows(DS, "enrollment"):
        a = admissions[e["admission_id"]]
        assert a["admission_status"] == "Accepted", e  # ADM-DQ-09
        season_end = years[a["admission_year_id"]]["end_date"]
        assert a["application_date"] <= e["enrollment_date"] <= season_end, e
        assert bool(e["student_id"]) == (e["enrollment_status"] == "Enrolled"), e  # ADM-DQ-10
        if e["student_id"]:  # ADM-DQ-11
            s = students[e["student_id"]]
            for col in ("applicant_id", "program_id", "admission_year_id"):
                assert s[col] == a[col], (e, col)

    for s in rows(DS, "student"):  # ADM-DQ-12
        if s["applicant_id"]:
            ap = applicants[s["applicant_id"]]
            person = (s["full_name"], s["date_of_birth"], s["gender"])
            assert person == (ap["full_name"], ap["date_of_birth"], ap["gender"]), s
