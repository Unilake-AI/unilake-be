"""Quy tắc nghiệp vụ Nhân sự (HR-DQ-04..12) trên dữ liệu mẫu.

Các kiểm tra schema, khóa và FK chung nằm trong test_dataset_schemas.py.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import pairwise

from tests.dataset_utils import index, load_schema, rows

DS = "hr"
TODAY = "2026-09-25"  # ngày chốt dữ liệu mẫu
RULES = load_schema(DS)["position_rules"]
DEGREE_ORDER = {"Cử nhân": 1, "Kỹ sư": 1, "Thạc sĩ": 2, "Tiến sĩ": 3}


def _departments() -> dict[str, dict[str, str]]:
    return index(DS, "department")


def _dept_type(code: str) -> str:
    if code in {"UNI", "BGH"}:
        return code
    return "BM" if code.startswith("BM") else code[0]


def _in_subtree(dept: str, root: str) -> bool:
    departments = _departments()
    while dept:
        if dept == root:
            return True
        dept = departments[dept]["parent_department_id"]
    return False


def _end(a: dict[str, str]) -> str:
    return a["end_date"] or "9999-12-31"


def _overlap(a: dict[str, str], b: dict[str, str]) -> bool:
    return a["start_date"] <= _end(b) and b["start_date"] <= _end(a)


def _by_employee() -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = defaultdict(list)
    for a in rows(DS, "assignment"):
        result[a["employee_id"]].append(a)
    return result


def test_department_tree() -> None:  # HR-DQ-04
    departments = _departments()
    roots = [d for d in departments.values() if not d["parent_department_id"]]
    assert len(roots) == 1, roots
    for dept_id, d in departments.items():
        seen, cur = set(), dept_id
        while cur:
            assert cur not in seen, f"cycle at {d['department_code']}"
            seen.add(cur)
            cur = departments[cur]["parent_department_id"]
        if d["department_code"].startswith("BM"):
            parent = departments[d["parent_department_id"]]
            assert parent["department_code"].startswith("K"), d


def test_employee_hire_dates() -> None:  # HR-DQ-05
    by_emp = _by_employee()
    for e in rows(DS, "employee"):
        age = int(e["hire_date"][:4]) - int(e["date_of_birth"][:4])
        assert 20 <= age <= 60, e
        assert by_emp[e["employee_id"]], f"{e['employee_code']} has no assignment"
        assert e["hire_date"] == min(a["start_date"] for a in by_emp[e["employee_id"]]), e


def test_assignment_timeline() -> None:  # HR-DQ-06, HR-DQ-07
    employees = index(DS, "employee")
    for emp_id, items in _by_employee().items():
        e = employees[emp_id]
        for a in items:
            assert a["start_date"] >= e["hire_date"], a
            assert not a["end_date"] or a["end_date"] >= a["start_date"], a
        primary = sorted(
            (a for a in items if a["is_primary"] == "true"), key=lambda a: a["start_date"]
        )
        for prev, nxt in pairwise(primary):
            assert not _overlap(prev, nxt), (prev, nxt)
        open_primary = [a for a in primary if _end(a) >= TODAY]
        if e["employee_status"] == "Active":
            assert len(open_primary) == 1, e
        else:
            assert all(a["end_date"] and a["end_date"] <= TODAY for a in items), e


def test_position_rules() -> None:  # HR-DQ-08, HR-DQ-09
    departments = _departments()
    positions = {k: v["position_code"] for k, v in index(DS, "position").items()}
    lecturers = set(index(DS, "lecturer"))
    held: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for a in rows(DS, "assignment"):
        pos = positions[a["position_id"]]
        dept_type = _dept_type(departments[a["department_id"]]["department_code"])
        assert dept_type in RULES["allowed_department_types"][pos], (pos, dept_type, a)
        if pos in RULES["lecturer_only"]:
            assert a["employee_id"] in lecturers, a
        if pos in RULES["staff_only"]:
            assert a["employee_id"] not in lecturers, a
        if pos in RULES["unique_holder_per_department"]:
            held[(a["department_id"], pos)].append(a)
    for key, items in held.items():
        for i, a in enumerate(items):
            for b in items[i + 1 :]:
                assert not _overlap(a, b), (key, a, b)


def test_qualification_rules() -> None:  # HR-DQ-10, HR-DQ-11
    employees = index(DS, "employee")
    names = {k: v["qualification_name"] for k, v in index(DS, "qualification").items()}
    degrees: dict[str, dict[str, int]] = defaultdict(dict)
    for q in rows(DS, "employee_qualification"):
        year = int(q["year_obtained"])
        birth = int(employees[q["employee_id"]]["date_of_birth"][:4])
        assert birth + 18 <= year <= int(TODAY[:4]), q
        name = names[q["qualification_id"]]
        if name in DEGREE_ORDER:
            degrees[q["employee_id"]][name] = year
    for emp_id, got in degrees.items():
        by_level = sorted(got.items(), key=lambda kv: DEGREE_ORDER[kv[0]])
        for (low, y1), (high, y2) in pairwise(by_level):
            if DEGREE_ORDER[low] < DEGREE_ORDER[high]:
                assert y1 < y2, (emp_id, low, high)
    for lec in rows(DS, "lecturer"):
        got = degrees[lec["lecturer_id"]]
        if lec["academic_rank"] in {"Ph.D.", "Assoc. Prof.", "Prof."}:
            assert "Tiến sĩ" in got, lec
        elif lec["academic_rank"] == "M.Sc.":
            assert "Thạc sĩ" in got, lec
            assert "Tiến sĩ" not in got, lec


def test_class_lecturers_are_employed_in_the_right_faculty() -> None:  # HR-DQ-12
    semesters = index("academic", "semester")
    courses = index("academic", "course")
    majors = index("admissions", "major")
    by_emp = _by_employee()
    for c in rows("academic", "class"):
        sem = semesters[c["semester_id"]]
        covering = [
            a
            for a in by_emp[c["lecturer_id"]]
            if a["start_date"] <= sem["start_date"] and _end(a) >= sem["end_date"]
        ]
        assert covering, f"{c['class_code']}: lecturer not employed for the whole semester"
        major_id = courses[c["course_id"]]["major_id"]
        if major_id and majors[major_id]["department_id"]:
            faculty = majors[major_id]["department_id"]
            assert any(_in_subtree(a["department_id"], faculty) for a in covering), c


def test_sample_has_every_status_and_a_concurrent_role() -> None:
    statuses = {e["employee_status"] for e in rows(DS, "employee")}
    assert statuses == {"Active", "Resigned", "Retired"}
    assert any(a["is_primary"] == "false" for a in rows(DS, "assignment"))
