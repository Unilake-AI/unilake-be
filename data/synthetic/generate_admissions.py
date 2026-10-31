"""Sinh dữ liệu mẫu Tuyển sinh giả lập có kết quả xác định (schema: unilake-db.dbml).

Cách chạy (từ thư mục gốc repo, sau generate_hr.py): python data/synthetic/generate_admissions.py
Ghi data/samples/admissions/*.csv và training/student.csv. Cùng seed cho cùng kết quả.
Mọi người, mã định danh, số điện thoại và email đều là giả lập.
"""

import csv
import random
import sys
import unicodedata
from datetime import date, timedelta
from pathlib import Path

ROOT = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else Path(__file__).resolve().parents[1] / "samples"
)
rng = random.Random(20260925)


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


def ascii_slug(s):
    s = s.replace("Đ", "D").replace("đ", "d")
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    ).lower()


# ---------- dữ liệu tham chiếu ----------
# department_id theo data/samples/hr/department.csv (generate_hr.py): 7 KCNTT, 8 KKT, 9 KNN, 10 KXD
majors = [
    (1, "7480201", "Công nghệ thông tin", 7),
    (2, "7480103", "Kỹ thuật phần mềm", 7),
    (3, "7460108", "Khoa học dữ liệu", 7),
    (4, "7340101", "Quản trị kinh doanh", 8),
    (5, "7340301", "Kế toán", 8),
    (6, "7220201", "Ngôn ngữ Anh", 9),
    (7, "7580201", "Kỹ thuật xây dựng", 10),
    (8, "8480101", "Khoa học máy tính", 7),
]
write(
    "admissions/major.csv",
    ["major_id", "major_code", "major_name", "department_id"],
    majors,
)

# program_id, mã, tên, major_id, bậc học, số năm, học phí, điểm chuẩn THPT 2024, trọng số phổ biến
programs = [
    (1, "CNTT-CQ", "Công nghệ thông tin", 1, "Bachelor", "4.0", "520000.00", 23.50, 22),
    (
        2,
        "CNTT-CLC",
        "Công nghệ thông tin - Chất lượng cao",
        1,
        "Bachelor",
        "4.0",
        "980000.00",
        21.00,
        10,
    ),
    (3, "KTPM-CQ", "Kỹ thuật phần mềm", 2, "Engineer", "4.5", "520000.00", 23.00, 14),
    (4, "KHDL-CQ", "Khoa học dữ liệu", 3, "Bachelor", "4.0", "540000.00", 24.00, 9),
    (5, "QTKD-CQ", "Quản trị kinh doanh", 4, "Bachelor", "4.0", "460000.00", 21.00, 13),
    (
        6,
        "QTKD-CLC",
        "Quản trị kinh doanh - Chất lượng cao",
        4,
        "Bachelor",
        "4.0",
        "900000.00",
        19.50,
        5,
    ),
    (7, "KT-CQ", "Kế toán", 5, "Bachelor", "4.0", "450000.00", 20.00, 8),
    (8, "NNA-CQ", "Ngôn ngữ Anh", 6, "Bachelor", "4.0", "440000.00", 22.00, 11),
    (9, "KTXD-KS", "Kỹ thuật xây dựng", 7, "Engineer", "4.5", "480000.00", 18.00, 6),
    (
        10,
        "KHMT-THS",
        "Thạc sĩ Khoa học máy tính",
        8,
        "Master",
        "2.0",
        "750000.00",
        None,
        0,
    ),
]
write(
    "admissions/program.csv",
    [
        "program_id",
        "program_code",
        "program_name",
        "major_id",
        "degree_level",
        "duration_years",
        "tuition_fee_per_credit",
    ],
    [p[:7] for p in programs],
)
ug = [p for p in programs if p[8]]

methods = [
    (
        1,
        "THPT",
        "Xét điểm thi tốt nghiệp THPT",
        "Tổng điểm 3 môn theo tổ hợp, thang 30, đã cộng điểm ưu tiên",
    ),
    (
        2,
        "HOCBA",
        "Xét học bạ THPT",
        "Điểm trung bình 3 môn lớp 12 theo tổ hợp, thang 30, đã cộng điểm ưu tiên",
    ),
    (
        3,
        "XTT",
        "Xét tuyển thẳng",
        "Theo quy chế tuyển sinh của Bộ GD&ĐT, không xét điểm",
    ),
]
write(
    "admissions/admission_method.csv",
    ["admission_method_id", "method_code", "method_name", "description"],
    methods,
)

YEARS = {1: 2024, 2: 2025, 3: 2026}
year_delta = {2024: 0.0, 2025: 0.5, 2026: 0.25}
write(
    "admissions/admission_year.csv",
    ["admission_year_id", "year_label", "start_date", "end_date"],
    [
        (i, f"{y}-{y + 1}", date(y, 3, 1).isoformat(), date(y, 9, 30).isoformat())
        for i, y in YEARS.items()
    ],
)

# tỉnh, mã tỉnh CCCD, trọng số, trường THPT
provinces = [
    (
        "Đà Nẵng",
        "048",
        30,
        [
            "THPT Phan Châu Trinh",
            "THPT Hoàng Hoa Thám",
            "THPT Chuyên Lê Quý Đôn",
            "THPT Thái Phiên",
        ],
    ),
    ("Huế", "046", 12, ["THPT Quốc Học", "THPT Hai Bà Trưng"]),
    ("Quảng Ngãi", "051", 12, ["THPT Trần Quốc Tuấn", "THPT Số 1 Tư Nghĩa"]),
    ("Quảng Trị", "045", 8, ["THPT Đông Hà", "THPT Lê Lợi"]),
    ("Gia Lai", "064", 8, ["THPT Pleiku", "THPT Hùng Vương"]),
    ("Đắk Lắk", "066", 8, ["THPT Buôn Ma Thuột", "THPT Chu Văn An"]),
    ("Khánh Hòa", "056", 6, ["THPT Lý Tự Trọng", "THPT Nguyễn Văn Trỗi"]),
    ("Nghệ An", "040", 8, ["THPT Huỳnh Thúc Kháng", "THPT Hà Huy Tập"]),
    ("Hà Nội", "001", 4, ["THPT Kim Liên", "THPT Việt Đức"]),
    (
        "Thành phố Hồ Chí Minh",
        "079",
        4,
        ["THPT Nguyễn Thị Minh Khai", "THPT Lê Quý Đôn"],
    ),
]
last = [
    "Nguyễn",
    "Trần",
    "Lê",
    "Phạm",
    "Hoàng",
    "Huỳnh",
    "Phan",
    "Võ",
    "Đặng",
    "Bùi",
    "Đỗ",
    "Hồ",
    "Ngô",
    "Dương",
]
last_w = [30, 12, 10, 8, 5, 5, 5, 6, 4, 4, 4, 3, 2, 2]
mid = {
    "Male": ["Văn", "Đức", "Minh", "Quốc", "Hữu", "Gia", "Thành"],
    "Female": ["Thị", "Ngọc", "Thu", "Khánh", "Bảo", "Minh", "Phương"],
}
first = {
    "Male": [
        "An",
        "Bảo",
        "Dũng",
        "Hùng",
        "Khoa",
        "Long",
        "Nam",
        "Phúc",
        "Quân",
        "Tuấn",
        "Huy",
        "Khang",
        "Nhật",
        "Thịnh",
    ],
    "Female": [
        "Anh",
        "Chi",
        "Hà",
        "Linh",
        "My",
        "Ngân",
        "Phương",
        "Trang",
        "Vy",
        "Yến",
        "Nhi",
        "Thảo",
        "Hân",
        "Uyên",
    ],
}

applicants, admissions, enrollments, students = [], [], [], []
used_ids, used_emails = set(), set()
aid = adm_id = enr_id = stu_id = 0
stu_seq = {}
PER_YEAR = 40

for year_id, y in YEARS.items():
    y_start, y_end = date(y, 3, 1), date(y, 9, 30)
    result_day = date(y, 8, 20)
    for _ in range(PER_YEAR):
        aid += 1
        gender = rng.choice(["Male", "Female"])
        full_name = f"{rng.choices(last, last_w)[0]} {rng.choice(mid[gender])} {rng.choice(first[gender])}"
        birth_year = y - 18 if rng.random() < 0.9 else y - 19
        dob = rand_date(date(birth_year, 1, 1), date(birth_year, 12, 31))
        prov, pcode, _, schools = rng.choices(provinces, [p[2] for p in provinces])[0]
        century_gender = {"Male": "2", "Female": "3"}[gender]  # sinh 2000-2099
        while True:
            nid = f"{pcode}{century_gender}{birth_year % 100:02d}{rng.randrange(10**6):06d}"
            if nid not in used_ids:
                used_ids.add(nid)
                break
        phone = (
            None
            if rng.random() < 0.08
            else rng.choice(["09", "03", "07", "08"]) + f"{rng.randrange(10**8):08d}"
        )
        email = None
        if rng.random() < 0.85:
            parts = ascii_slug(full_name).split()
            base = (
                f"{parts[-1]}.{''.join(p[0] for p in parts[:-1])}{dob.strftime('%d%m')}"
            )
            email = (
                f"{base}@example.com"
                if base not in used_emails
                else f"{base}{aid}@example.com"
            )
            used_emails.add(base)
        school = None if rng.random() < 0.05 else rng.choice(schools)
        applicants.append(
            (
                aid,
                nid,
                full_name,
                dob.isoformat(),
                gender,
                phone,
                email,
                school,
                None if rng.random() < 0.03 else prov,
            )
        )

        # ---------- nguyện vọng ----------
        ability = rng.gauss(0, 1)
        n_wish = rng.choices([1, 2, 3], [30, 40, 30])[0]
        wishes = []
        while len(wishes) < n_wish:
            prog = rng.choices(ug, [p[8] for p in ug])[0]
            if ability > 1.4 and not wishes and rng.random() < 0.4:
                method = 3
            else:
                method = rng.choices([1, 2], [55, 45])[0]
            if all((prog[0], method) != (w[0][0], w[1]) for w in wishes):
                wishes.append((prog, method))

        accepted_done = False
        for prog, method in wishes:
            cutoff = prog[7] + year_delta[y]
            if method == 1:
                app_date = rand_date(date(y, 7, 16), date(y, 7, 28))
                score = (
                    round(
                        min(29.5, max(12.0, 21.0 + 3.3 * ability + rng.gauss(0, 1.0)))
                        * 20
                    )
                    / 20
                )
            elif method == 2:
                app_date = rand_date(date(y, 3, 15), date(y, 6, 15))
                cutoff = min(28.5, cutoff + 2.0)
                score = round(
                    min(29.8, max(17.0, 24.3 + 2.2 * ability + rng.gauss(0, 0.8))), 1
                )
            else:
                app_date = rand_date(date(y, 4, 1), date(y, 6, 20))
                score = None
            if method == 3:
                eligible = ability > 1.4
            else:
                assert (
                    score is not None
                )  # Phương thức 1 và 2 luôn tính ra điểm dạng số.
                eligible = score >= cutoff
            if eligible and not accepted_done:
                status, accepted_done = "Accepted", True
            elif eligible:
                status = "Rejected"  # đã trúng nguyện vọng ưu tiên cao hơn
            elif score is not None and score >= cutoff - 0.5 and rng.random() < 0.5:
                status = "Waitlisted"
            else:
                status = "Rejected"
            adm_id += 1
            admissions.append(
                (
                    adm_id,
                    aid,
                    prog[0],
                    method,
                    year_id,
                    app_date.isoformat(),
                    None if score is None else f"{score:.2f}",
                    status,
                )
            )

            if status == "Accepted":
                enr_id += 1
                r = rng.random()
                estatus = (
                    "Enrolled"
                    if r < 0.82
                    else ("Deferred" if r < 0.88 else "Cancelled")
                )
                student_id = None
                if estatus == "Enrolled":
                    stu_id += 1
                    stu_seq[y] = stu_seq.get(y, 0) + 1
                    student_id = stu_id
                    age = 2026 - y
                    sr = rng.random()
                    sstatus = (
                        "Active"
                        if age == 0 or sr < (0.86 if age == 2 else 0.93)
                        else ("Suspended" if sr < 0.95 else "Dropped")
                    )
                    students.append(
                        (
                            stu_id,
                            f"{y}{prog[0]:02d}{stu_seq[y]:04d}",
                            aid,
                            full_name,
                            dob.isoformat(),
                            gender,
                            prog[0],
                            year_id,
                            sstatus,
                        )
                    )
                enrollments.append(
                    (
                        enr_id,
                        adm_id,
                        student_id,
                        rand_date(
                            result_day + timedelta(days=2), date(y, 9, 12)
                        ).isoformat(),
                        estatus,
                    )
                )

        # xét tuyển bổ sung mùa hiện tại (2026): hồ sơ còn đang xử lý
        if y == 2026 and not accepted_done and rng.random() < 0.35:
            taken = {(w[0][0], w[1]) for w in wishes}
            prog = rng.choice([p for p in ug if (p[0], 1) not in taken])
            adm_id += 1
            score = (
                round(
                    min(29.5, max(12.0, 21.0 + 3.3 * ability + rng.gauss(0, 1.0))) * 20
                )
                / 20
            )
            admissions.append(
                (
                    adm_id,
                    aid,
                    prog[0],
                    1,
                    year_id,
                    rand_date(date(y, 9, 5), date(y, 9, 20)).isoformat(),
                    f"{score:.2f}",
                    "Applied",
                )
            )

write(
    "admissions/applicant.csv",
    [
        "applicant_id",
        "national_id",
        "full_name",
        "date_of_birth",
        "gender",
        "phone",
        "email",
        "high_school_name",
        "province",
    ],
    applicants,
)
write(
    "admissions/admission.csv",
    [
        "admission_id",
        "applicant_id",
        "program_id",
        "admission_method_id",
        "admission_year_id",
        "application_date",
        "admission_score",
        "admission_status",
    ],
    admissions,
)
write(
    "training/student.csv",
    [
        "student_id",
        "student_code",
        "applicant_id",
        "full_name",
        "date_of_birth",
        "gender",
        "program_id",
        "admission_year_id",
        "student_status",
    ],
    students,
)
write(
    "admissions/enrollment.csv",
    [
        "enrollment_id",
        "admission_id",
        "student_id",
        "enrollment_date",
        "enrollment_status",
    ],
    enrollments,
)
print(len(applicants), len(admissions), len(enrollments), len(students))
