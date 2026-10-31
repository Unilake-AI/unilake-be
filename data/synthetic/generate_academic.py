"""Sinh dữ liệu mẫu Đào tạo giả lập có kết quả xác định (schema: unilake-db.dbml).

Cách chạy (từ thư mục gốc repo, sau generate_hr.py và generate_admissions.py):
    python data/synthetic/generate_academic.py
Đọc data/samples/{hr,admissions,training}/*.csv (sinh viên từ enrollment của Tuyển sinh,
giảng viên từ assignment của Nhân sự) và ghi training/{semester,course,class,enrollment_grade}.csv.
Cùng seed cho cùng kết quả. Mọi người, số điện thoại và email đều là giả lập.
"""

import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else Path(__file__).resolve().parents[1] / "samples"
)
rng = random.Random(20260926)
TODAY = date(2026, 9, 25)


def read(rel):
    with open(ROOT / rel, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write(rel, header, rows):
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])


def rand_date(a, b):
    return a + timedelta(days=rng.randrange((b - a).days + 1))


def q(x, step):
    return round(x / step) * step


# ---------- semester: HK1 (-1), HK2 (-2), học kỳ hè (-3) ----------
semesters = []  # (id, mã, năm học, bắt đầu, kết thúc, loại, năm)
sid = 0
for y in (2024, 2025, 2026):
    for kind, start, end in (
        (1, date(y, 9, 9), date(y + 1, 1, 19)),
        (2, date(y + 1, 2, 17), date(y + 1, 6, 29)),
        (3, date(y + 1, 7, 7), date(y + 1, 8, 17)),
    ):
        if start > TODAY:
            continue
        sid += 1
        semesters.append((sid, f"{y}-{kind}", f"{y}-{y + 1}", start, end, kind, y))
write(
    "training/semester.csv",
    ["semester_id", "semester_code", "academic_year", "start_date", "end_date"],
    [(s[0], s[1], s[2], s[3].isoformat(), s[4].isoformat()) for s in semesters],
)
regular = [s for s in semesters if s[5] != 3]  # Theo thứ tự HK1, HK2, HK1...
summer = {s[6]: s for s in semesters if s[5] == 3}

# ---------- học phần ----------
# mã: (tên, tín chỉ, major_id hoặc None, độ khó)
COURSES = {
    "GEN101": ("Triết học Mác - Lênin", 3, None, 0.0),
    "GEN102": ("Kinh tế chính trị Mác - Lênin", 2, None, 0.1),
    "GEN103": ("Chủ nghĩa xã hội khoa học", 2, None, 0.2),
    "GEN104": ("Tư tưởng Hồ Chí Minh", 2, None, 0.3),
    "GEN105": ("Pháp luật đại cương", 2, None, 0.3),
    "GEN106": ("Lịch sử Đảng Cộng sản Việt Nam", 2, None, 0.2),
    "ENG101": ("Tiếng Anh 1", 3, None, 0.0),
    "ENG102": ("Tiếng Anh 2", 3, None, -0.2),
    "ENG201": ("Tiếng Anh 3", 3, None, -0.3),
    "MTH101": ("Toán cao cấp", 3, None, -0.8),
    "MTH102": ("Xác suất thống kê", 3, None, -0.6),
    "CS101": ("Nhập môn lập trình", 3, 1, -0.5),
    "CS102": ("Cấu trúc dữ liệu và giải thuật", 3, 1, -0.9),
    "CS201": ("Cơ sở dữ liệu", 3, 1, -0.3),
    "CS202": ("Mạng máy tính", 3, 1, -0.4),
    "CS203": ("Hệ điều hành", 3, 1, -0.6),
    "CS204": ("Lập trình hướng đối tượng", 3, 1, -0.4),
    "CS301": ("Công nghệ phần mềm", 3, 1, 0.0),
    "CS302": ("Trí tuệ nhân tạo", 3, 1, -0.5),
    "SE201": ("Phân tích và thiết kế hệ thống", 3, 2, -0.2),
    "SE202": ("Kiểm thử phần mềm", 3, 2, 0.0),
    "SE301": ("Quản lý dự án phần mềm", 3, 2, 0.2),
    "SE302": ("Phát triển ứng dụng web", 3, 2, 0.1),
    "DS201": ("Nhập môn khoa học dữ liệu", 3, 3, -0.2),
    "DS202": ("Học máy", 3, 3, -0.8),
    "DS203": ("Trực quan hóa dữ liệu", 2, 3, 0.2),
    "DS301": ("Khai phá dữ liệu", 3, 3, -0.5),
    "DS302": ("Dữ liệu lớn", 3, 3, -0.4),
    "BUS101": ("Kinh tế vi mô", 3, 4, -0.3),
    "BUS102": ("Kinh tế vĩ mô", 3, 4, -0.3),
    "BUS103": ("Quản trị học", 3, 4, 0.2),
    "BUS201": ("Marketing căn bản", 3, 4, 0.2),
    "BUS202": ("Quản trị nguồn nhân lực", 3, 4, 0.2),
    "BUS203": ("Quản trị tài chính doanh nghiệp", 3, 4, -0.3),
    "BUS301": ("Quản trị chiến lược", 3, 4, 0.0),
    "BUS302": ("Hành vi tổ chức", 3, 4, 0.2),
    "ACC101": ("Nguyên lý kế toán", 3, 5, -0.3),
    "ACC201": ("Kế toán tài chính 1", 3, 5, -0.5),
    "ACC202": ("Kế toán tài chính 2", 3, 5, -0.5),
    "ACC203": ("Kế toán quản trị", 3, 5, -0.3),
    "ACC301": ("Kiểm toán căn bản", 3, 5, -0.2),
    "ACC302": ("Thuế", 2, 5, 0.0),
    "LNG101": ("Nghe - Nói 1", 3, 6, 0.3),
    "LNG102": ("Đọc - Viết 1", 3, 6, 0.2),
    "LNG103": ("Nghe - Nói 2", 3, 6, 0.1),
    "LNG104": ("Đọc - Viết 2", 3, 6, 0.0),
    "LNG201": ("Ngữ âm - Âm vị học", 2, 6, -0.3),
    "LNG202": ("Ngữ pháp tiếng Anh", 3, 6, -0.2),
    "LNG203": ("Biên dịch 1", 3, 6, -0.2),
    "LNG204": ("Văn hóa Anh - Mỹ", 2, 6, 0.3),
    "LNG301": ("Phiên dịch 1", 3, 6, -0.4),
    "LNG302": ("Tiếng Anh thương mại", 3, 6, 0.1),
    "CE101": ("Hình họa - Vẽ kỹ thuật", 3, 7, -0.3),
    "CE102": ("Cơ học lý thuyết", 3, 7, -0.8),
    "CE103": ("Vật liệu xây dựng", 2, 7, 0.0),
    "CE201": ("Sức bền vật liệu", 3, 7, -1.0),
    "CE202": ("Cơ học đất", 3, 7, -0.7),
    "CE203": ("Kết cấu bê tông cốt thép 1", 3, 7, -0.6),
    "CE204": ("Trắc địa", 2, 7, 0.0),
    "CE301": ("Kết cấu thép", 3, 7, -0.6),
    "CE302": ("Nền móng", 3, 7, -0.5),
    "MS601": ("Thuật toán nâng cao", 3, 8, -0.5),
}
course_ids = {code: i for i, code in enumerate(COURSES, 1)}
write(
    "training/course.csv",
    ["course_id", "course_code", "course_name", "credit_hours", "major_id"],
    [(course_ids[c], c, v[0], v[1], v[2]) for c, v in COURSES.items()],
)

# Chương trình khung (học kỳ chính thứ 1..5) theo major_id. DB nguồn chưa có bảng này.
COMMON = {
    1: ["GEN101", "ENG101", "MTH101"],
    2: ["GEN102", "ENG102", "MTH102"],
    3: ["GEN103", "ENG201"],
    4: ["GEN104", "GEN105"],
    5: ["GEN106"],
}
IT_BASE = {1: ["CS101"], 2: ["CS102"]}
MAJOR_PLAN = {
    1: {**IT_BASE, 3: ["CS201", "CS202"], 4: ["CS203", "CS204"], 5: ["CS301", "CS302"]},
    2: {**IT_BASE, 3: ["SE201", "CS201"], 4: ["SE202", "CS204"], 5: ["SE301", "SE302"]},
    3: {**IT_BASE, 3: ["DS201", "CS201"], 4: ["DS202", "DS203"], 5: ["DS301", "DS302"]},
    4: {
        1: ["BUS101"],
        2: ["BUS102", "BUS103"],
        3: ["BUS201", "ACC101"],
        4: ["BUS202", "BUS203"],
        5: ["BUS301", "BUS302"],
    },
    5: {
        1: ["BUS101"],
        2: ["BUS102", "ACC101"],
        3: ["ACC201", "BUS201"],
        4: ["ACC202", "ACC203"],
        5: ["ACC301", "ACC302"],
    },
    6: {
        1: ["LNG101", "LNG102"],
        2: ["LNG103", "LNG104"],
        3: ["LNG201", "LNG202"],
        4: ["LNG203", "LNG204"],
        5: ["LNG301", "LNG302"],
    },
    7: {
        1: ["CE101"],
        2: ["CE102", "CE103"],
        3: ["CE201", "CE202"],
        4: ["CE203", "CE204"],
        5: ["CE301", "CE302"],
    },
}


def plan(major_id, idx):
    return COMMON[idx] + MAJOR_PLAN[major_id][idx]


# ---------- giảng viên (từ HR): nhóm học phần -> khoa phụ trách ----------
GROUP_FACULTY = {
    "GEN": "KLLCT",
    "MTH": "KKHCB",
    "ENG": "KNN",
    "LNG": "KNN",
    "CS": "KCNTT",
    "SE": "KCNTT",
    "DS": "KCNTT",
    "BUS": "KKT",
    "ACC": "KKT",
    "CE": "KXD",
}
departments = {d["department_id"]: d for d in read("hr/department.csv")}
dept_by_code = {d["department_code"]: d["department_id"] for d in departments.values()}
lecturer_ids = {row["lecturer_id"] for row in read("hr/lecturer.csv")}
lecturer_assignments = [
    a for a in read("hr/assignment.csv") if a["employee_id"] in lecturer_ids
]


def in_subtree(dept, root):
    while dept:
        if dept == root:
            return True
        dept = departments[dept]["parent_department_id"]
    return False


def lecturer_for(code, sem):
    """Giảng viên có phân công ở khoa phụ trách, còn công tác suốt học kỳ."""
    group = next(
        g for g in sorted(GROUP_FACULTY, key=len, reverse=True) if code.startswith(g)
    )
    faculty = dept_by_code[GROUP_FACULTY[group]]
    start, end = sem[3].isoformat(), sem[4].isoformat()
    candidates = sorted(
        {
            int(a["employee_id"])
            for a in lecturer_assignments
            if in_subtree(a["department_id"], faculty)
            and a["start_date"] <= start
            and (not a["end_date"] or a["end_date"] >= end)
        }
    )
    return rng.choice(candidates)


# ---------- sinh viên (từ Tuyển sinh) ----------
students = read("training/student.csv")
program_major = {
    p["program_id"]: int(p["major_id"]) for p in read("admissions/program.csv")
}
year_start = {
    y["admission_year_id"]: int(y["year_label"][:4])
    for y in read("admissions/admission_year.csv")
}
admissions = {a["admission_id"]: a for a in read("admissions/admission.csv")}
enroll_date = {}  # applicant_id → ngày nhập học
entry_score = {}  # applicant_id → điểm xét tuyển trên thang 30
for e in read("admissions/enrollment.csv"):
    if e["enrollment_status"] == "Enrolled":
        a = admissions[e["admission_id"]]
        enroll_date[a["applicant_id"]] = date.fromisoformat(e["enrollment_date"])
        if a["admission_score"]:
            entry_score[a["applicant_id"]] = float(a["admission_score"])


def fmt(v):
    return None if v is None else f"{v:.2f}"


LETTERS = [
    (8.5, "A"),
    (8.0, "B+"),
    (7.0, "B"),
    (6.5, "C+"),
    (5.5, "C"),
    (5.0, "D+"),
    (4.0, "D"),
    (0, "F"),
]


def letter(g):
    return next(lt for cut, lt in LETTERS if g >= cut)


# ---------- đăng ký học phần ----------
regs = []  # (student_id, course_code, dòng học kỳ, điểm hoặc None, ngày đăng ký)
for s in students:
    cohort = year_start[s["admission_year_id"]]
    major = program_major[s["program_id"]]
    score = entry_score.get(s["applicant_id"])
    ability = (
        (score - 23.5) / 3.0 * 0.6 + rng.gauss(0, 0.8) if score else rng.gauss(0.5, 0.8)
    )
    my_regular = [sem for sem in regular if sem[6] >= cohort]
    stop_after = None
    if s["student_status"] == "Dropped":
        stop_after = rng.choice([1, 2])  # thôi học sau HK thứ 1 hoặc 2
        ability -= 1.5
    elif s["student_status"] == "Suspended":
        stop_after = len(my_regular) - 1  # bảo lưu từ học kỳ hiện tại
    for idx, sem in enumerate(my_regular, 1):
        if stop_after is not None and idx > stop_after:
            break
        if idx == 1:
            reg_date = enroll_date[s["applicant_id"]]
        else:
            reg_date = sem[3] - timedelta(days=rng.randrange(7, 22))
        for code in plan(major, idx):
            regs.append((int(s["student_id"]), code, sem, ability, reg_date))

# Chấm điểm các học phần đã kết thúc, gom các trường hợp trượt để học lại vào hè
rows = []  # (student_id, mã, học kỳ, giữa kỳ, cuối kỳ, điểm, điểm chữ, ngày đăng ký)
failed = []
for student_id, code, sem, ability, reg_date in regs:
    if sem[4] >= TODAY:  # học kỳ đang diễn ra: chưa có điểm
        rows.append((student_id, code, sem, None, None, None, None, reg_date))
        continue
    base = 6.4 + 1.2 * ability + COURSES[code][3]
    mid = min(10.0, max(0.0, q(base + rng.gauss(0, 1.0), 0.5)))
    final = min(10.0, max(0.0, q(base + rng.gauss(0, 1.6), 0.25)))
    grade = round(0.3 * mid + 0.7 * final + 1e-9, 1)
    rows.append((student_id, code, sem, mid, final, grade, letter(grade), reg_date))
    if grade < 4.0:
        failed.append((student_id, code, sem, ability))

for student_id, code, sem, ability in failed:  # học lại ở học kỳ hè cùng năm học
    hs = summer.get(sem[6])
    if not hs or rng.random() < 0.25:  # một số sinh viên chưa học lại
        continue
    base = 6.4 + 1.2 * ability + COURSES[code][3] + 1.0
    mid = min(10.0, max(0.0, q(base + rng.gauss(0, 1.0), 0.5)))
    final = min(10.0, max(0.0, q(base + rng.gauss(0, 1.2), 0.25)))
    grade = round(0.3 * mid + 0.7 * final + 1e-9, 1)
    rows.append(
        (
            student_id,
            code,
            hs,
            mid,
            final,
            grade,
            letter(grade),
            hs[3] - timedelta(days=rng.randrange(5, 15)),
        )
    )

# ---------- lớp học phần: một lớp cho mỗi (học phần, học kỳ), tách khi vượt sĩ số ----------
BUILDINGS = ["A", "B", "C"]
groups = {}
for r in rows:
    groups.setdefault((r[1], r[2][0]), []).append(r)
classes, grade_rows = [], []
sem_by_id = {s[0]: s for s in semesters}
for (code, sem_id), members in sorted(
    groups.items(), key=lambda kv: (kv[0][1], course_ids[kv[0][0]])
):
    sem = sem_by_id[sem_id]
    capacity = 30 if sem[5] == 3 else (60 if code[:3] in ("GEN", "ENG", "MTH") else 45)
    members.sort(key=lambda r: r[0])
    for sec, start in enumerate(range(0, len(members), capacity), 1):
        cid = len(classes) + 1
        room = (
            None
            if sem[5] == 3 and rng.random() < 0.3
            else (
                f"{rng.choice(BUILDINGS)}{rng.randrange(1, 6)}{rng.randrange(1, 12):02d}"
            )
        )
        classes.append(
            (
                cid,
                f"{code}-{sem[1]}-{sec:02d}",
                course_ids[code],
                sem_id,
                lecturer_for(code, sem),
                capacity,
                room,
            )
        )
        for r in members[start : start + capacity]:
            grade_rows.append(
                (r[0], cid, fmt(r[3]), fmt(r[4]), fmt(r[5]), r[6], r[7].isoformat())
            )

write(
    "training/class.csv",
    [
        "class_id",
        "class_code",
        "course_id",
        "semester_id",
        "lecturer_id",
        "max_capacity",
        "room",
    ],
    classes,
)
grade_rows.sort()
write(
    "training/enrollment_grade.csv",
    [
        "student_id",
        "class_id",
        "midterm_score",
        "final_score",
        "grade_value",
        "letter_grade",
        "registration_date",
    ],
    grade_rows,
)
print(len(semesters), len(COURSES), len(classes), len(grade_rows), len(failed))
