"""Build deterministic, schema-aligned samples from the synthetic source generators.

Run from the repository root: python data/synthetic/generate_samples.py [output-directory]
Legacy source generators run in a temporary directory. Only current schema entities
are published; the admissions confirmation export is used to link students to admissions.
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parents[2]
AS_OF = "2026-09-25"
CREATED_AT = AS_OF + "T00:00:00"


def read(root: Path, name: str) -> list[dict[str, str]]:
    with (root / name).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def build(source: Path, output: Path) -> None:
    schemas = {
        domain: json.loads((REPO / f"docs/data-schema/{domain}_v1.schema.json").read_text())
        for domain in ("hr", "admissions", "academic")
    }
    specs = {
        name: spec
        for schema in schemas.values()
        for name, spec in schema["entities"].items()
        if "columns" in spec
    }
    records = {
        name: read(source, spec["file"])
        for name, spec in specs.items()
        if (source / spec["file"]).exists()
    }
    for row in records["department"]:
        code = row["department_code"]
        row.update(
            department_type="University"
            if code == "UNI"
            else "Board"
            if code == "BGH"
            else "Division"
            if code.startswith("BM")
            else "Faculty"
            if code.startswith("K")
            else "Office",
            status="Active",
            created_at=CREATED_AT,
        )
    for name in ("major", "program", "course"):
        for row in records[name]:
            row["status"] = "Active"
    for row in records["admission_method"]:
        row.update(
            max_score={"THPT": "30.00", "HOCBA": "30.00", "DGNL": "1200.00", "XTT": "0.00"}[
                row["method_code"]
            ],
            is_active="true",
        )
    for row in records["admission_year"]:
        row.update(
            status="Upcoming"
            if row["start_date"] > AS_OF
            else "Closed"
            if row["end_date"] < AS_OF
            else "Open",
            created_at=CREATED_AT,
        )
    for row in records["applicant"]:
        row["created_at"] = CREATED_AT
    ranks: Counter[tuple[str, str]] = Counter()
    for row in records["admission"]:
        key = (row["applicant_id"], row["admission_year_id"])
        ranks[key] += 1
        row.update(preference_rank=str(ranks[key]), created_at=CREATED_AT)
    confirmations = {
        row["student_id"]: row
        for row in read(source, "admissions/enrollment.csv")
        if row["student_id"]
    }
    for row in records["student"]:
        row.update(
            admission_id=confirmations[row["student_id"]]["admission_id"], created_at=CREATED_AT
        )
    for row in records["lecturer"]:
        rank = row.pop("academic_rank")
        row.update(
            employee_id=row.pop("lecturer_id"),
            academic_degree="Ph.D." if rank in {"Ph.D.", "Assoc. Prof.", "Prof."} else rank,
            academic_title=rank if rank in {"Assoc. Prof.", "Prof."} else "",
        )
    for row in records["assignment"]:
        row["assignment_status"] = (
            "Ended" if row["end_date"] and row["end_date"] < AS_OF else "Active"
        )
    for row in records["semester"]:
        row["status"] = (
            "Planned"
            if row["start_date"] > AS_OF
            else "Completed"
            if row["end_date"] < AS_OF
            else "Ongoing"
        )
    semesters = {row["semester_id"]: row for row in records["semester"]}
    classes = {row["class_id"]: row for row in records["class"]}
    records["class_lecturer"] = []
    for i, row in enumerate(records["class"], 1):
        sem = semesters[row["semester_id"]]
        row["class_status"] = {
            "Planned": "Open",
            "Completed": "Completed",
            "Ongoing": "InProgress",
        }[sem["status"]]
        records["class_lecturer"].append(
            {
                "class_lecturer_id": str(i),
                "class_id": row["class_id"],
                "lecturer_id": row.pop("lecturer_id"),
                "teaching_role": "Lecturer",
                "is_primary": "true",
                "assigned_from": sem["start_date"],
                "assigned_to": sem["end_date"],
            }
        )
    records["enrollment"] = []
    bands = schemas["academic"]["grading"]["letter_bands"]
    for i, row in enumerate(records["enrollment_grade"], 1):
        sem = semesters[classes[row["class_id"]]["semester_id"]]
        records["enrollment"].append(
            {
                "enrollment_id": str(i),
                "student_id": row.pop("student_id"),
                "class_id": row.pop("class_id"),
                "enrollment_date": row["registration_date"],
                "enrollment_status": "Completed" if sem["status"] == "Completed" else "Registered",
                "created_at": CREATED_AT,
            }
        )
        total = row["grade_value"]
        band = next((b for b in bands if total and Decimal(total) >= Decimal(str(b["min"]))), None)
        row.update(
            enrollment_grade_id=str(i),
            enrollment_id=str(i),
            total_score=total,
            grade_value=str(band["grade_value"]) if band else "",
        )
    # Publish a versioned curriculum per program; course membership follows the
    # source generator's major assignment, including common general courses.
    records["curriculum"], records["curriculum_course"] = [], []
    for program in records["program"]:
        cid = program["program_id"]
        records["curriculum"].append(
            {
                "curriculum_id": cid,
                "program_id": cid,
                "curriculum_code": f"{program['program_code']}-2024",
                "curriculum_name": f"Khung {program['program_name']}",
                "version": "1",
                "effective_from": "2024-01-01",
                "effective_to": "",
                "status": "Active",
            }
        )
        for course in records["course"]:
            if course["major_id"] and course["major_id"] != program["major_id"]:
                continue
            records["curriculum_course"].append(
                {
                    "curriculum_course_id": str(len(records["curriculum_course"]) + 1),
                    "curriculum_id": cid,
                    "course_id": course["course_id"],
                    "semester_no": "1",
                    "course_type": "Major" if course["major_id"] else "General",
                    "is_required": "true",
                    "prerequisite_course_id": "",
                }
            )
    for name, spec in specs.items():
        path = output / spec["file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        columns = [col["name"] for col in spec["columns"]]
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=columns, lineterminator="\n", extrasaction="ignore"
            )
            writer.writeheader()
            for row in records[name]:
                # Missing required fields fail generation instead of silently
                # publishing incomplete samples.
                for col in spec["columns"]:
                    if not col["nullable"] and not row.get(col["name"]):
                        raise ValueError(f"Missing {name}.{col['name']}")
                writer.writerow(row)


def main() -> None:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "data/samples"
    with TemporaryDirectory(prefix="unilake-samples-") as directory:
        source = Path(directory)
        for domain in ("hr", "admissions", "academic"):
            subprocess.run(
                [sys.executable, str(REPO / f"data/synthetic/generate_{domain}.py"), str(source)],
                check=True,
            )
        build(source, output)
    print(f"Published current-schema samples to {output}")


if __name__ == "__main__":
    main()
