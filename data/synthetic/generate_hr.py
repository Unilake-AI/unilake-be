"""Sinh dữ liệu mẫu Nhân sự giả lập có kết quả xác định (schema: unilake-db.dbml).

Cách chạy (từ thư mục gốc repo), chạy trước vì mẫu Tuyển sinh và Đào tạo tham chiếu các dòng này:
    python data/synthetic/generate_hr.py
    python data/synthetic/generate_admissions.py
    python data/synthetic/generate_academic.py
Ghi data/samples/hr/*.csv. Cùng seed cho cùng kết quả. Mọi người, số điện thoại và email đều là giả lập.
"""

import csv
import random
import sys
import unicodedata
from datetime import date
from pathlib import Path

ROOT = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else Path(__file__).resolve().parents[1] / "samples"
)
rng = random.Random(20260927)


def write(rel, header, rows):
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])


def ascii_slug(s):
    s = s.replace("Đ", "D").replace("đ", "d")
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    ).lower()


# ---------- department: trường -> phòng/ban/khoa -> bộ môn ----------
DEPARTMENTS = [
    ("UNI", "Trường Đại học UniLake", None),
    ("BGH", "Ban Giám hiệu", "UNI"),
    ("PDT", "Phòng Đào tạo", "UNI"),
    ("PTS", "Phòng Tuyển sinh và Truyền thông", "UNI"),
    ("PTCNS", "Phòng Tổ chức - Nhân sự", "UNI"),
    ("PKHTC", "Phòng Kế hoạch - Tài chính", "UNI"),
    ("KCNTT", "Khoa Công nghệ Thông tin", "UNI"),
    ("KKT", "Khoa Kinh tế", "UNI"),
    ("KNN", "Khoa Ngoại ngữ", "UNI"),
    ("KXD", "Khoa Xây dựng", "UNI"),
    ("KLLCT", "Khoa Lý luận chính trị", "UNI"),
    ("KKHCB", "Khoa Khoa học cơ bản", "UNI"),
    ("BMKHMT", "Bộ môn Khoa học máy tính", "KCNTT"),
    ("BMHTTT", "Bộ môn Hệ thống thông tin", "KCNTT"),
]
dept_id = {code: i for i, (code, _, _) in enumerate(DEPARTMENTS, 1)}
write(
    "hr/department.csv",
    ["department_id", "department_code", "department_name", "parent_department_id"],
    [(dept_id[c], c, n, dept_id.get(p)) for c, n, p in DEPARTMENTS],
)

# ---------- chức vụ ----------
POSITIONS = [
    ("HT", "Hiệu trưởng", "Leadership"),
    ("PHT", "Phó Hiệu trưởng", "Leadership"),
    ("TK", "Trưởng khoa", "Management"),
    ("PTK", "Phó Trưởng khoa", "Management"),
    ("TP", "Trưởng phòng", "Management"),
    ("TBM", "Trưởng bộ môn", "Management"),
    ("GVC", "Giảng viên chính", "Staff"),
    ("GV", "Giảng viên", "Staff"),
    ("TG", "Trợ giảng", None),
    ("CV", "Chuyên viên", "Staff"),
    ("NV", "Nhân viên", "Staff"),
]
pos_id = {code: i for i, (code, _, _) in enumerate(POSITIONS, 1)}
write(
    "hr/position.csv",
    ["position_id", "position_code", "position_name", "position_level"],
    [(pos_id[c], c, n, lv) for c, n, lv in POSITIONS],
)

# ---------- trình độ ----------
QUALIFICATIONS = [
    ("BA", "Cử nhân", "Degree"),
    ("ENG", "Kỹ sư", "Degree"),
    ("MSC", "Thạc sĩ", "Degree"),
    ("PHD", "Tiến sĩ", "Degree"),
    ("NVSP", "Chứng chỉ Nghiệp vụ sư phạm", "Certificate"),
    ("IELTS", "IELTS Academic", "Certificate"),
    ("LLCT", "Cao cấp lý luận chính trị", "Certificate"),
    ("KTT", "Chứng chỉ Kế toán trưởng", "Certificate"),
    ("AWS", "AWS Certified Solutions Architect - Associate", "Certificate"),
]
qual_id = {code: i for i, (code, _, _) in enumerate(QUALIFICATIONS, 1)}
write(
    "hr/qualification.csv",
    ["qualification_id", "qualification_name", "qualification_type"],
    [(qual_id[c], n, t) for c, n, t in QUALIFICATIONS],
)

# ---------- nhân viên ----------
BKDN = "Trường ĐH Bách khoa - ĐH Đà Nẵng"
KTDN = "Trường ĐH Kinh tế - ĐH Đà Nẵng"
NNDN = "Trường ĐH Ngoại ngữ - ĐH Đà Nẵng"
SPDN = "Trường ĐH Sư phạm - ĐH Đà Nẵng"
LAWH = "Trường ĐH Luật - ĐH Huế"
SP = "Trường ĐH Sư phạm - ĐH Huế"
NVSP_ORG = "Trường ĐH Sư phạm - ĐH Đà Nẵng"

# (tên, giới tính, ngày sinh, giảng viên (học hàm, lĩnh vực nghiên cứu) hoặc None, trạng thái,
#  phân công [(đơn vị, chức vụ, bắt đầu, kết thúc, là chính)],
#  trình độ [(mã, năm, cơ sở đào tạo)])
P = True
S = False
EMPLOYEES = [
    (
        "Nguyễn Hữu Phước",
        "Male",
        "1968-05-12",
        ("Assoc. Prof.", "Khoa học máy tính"),
        "Active",
        [
            ("BMKHMT", "GV", "1995-08-15", "2005-06-30", P),
            ("BMKHMT", "GVC", "2005-07-01", "2015-06-30", P),
            ("BGH", "PHT", "2015-07-01", "2020-06-30", P),
            ("BGH", "HT", "2020-07-01", None, P),
            ("BMKHMT", "GVC", "2015-07-01", None, S),
        ],
        [
            ("ENG", 1990, "Trường ĐH Bách khoa Hà Nội"),
            ("MSC", 1996, BKDN),
            ("PHD", 2003, "Asian Institute of Technology"),
            ("NVSP", 1996, NVSP_ORG),
            ("LLCT", 2016, "Học viện Chính trị Quốc gia Hồ Chí Minh"),
        ],
    ),
    (
        "Trần Thị Mỹ Hạnh",
        "Female",
        "1972-09-03",
        ("Ph.D.", "Kinh tế phát triển"),
        "Active",
        [
            ("KKT", "GV", "1998-08-15", "2008-06-30", P),
            ("KKT", "GVC", "2008-07-01", "2020-06-30", P),
            ("BGH", "PHT", "2020-07-01", None, P),
            ("KKT", "GVC", "2020-07-01", None, S),
        ],
        [
            ("BA", 1994, KTDN),
            ("MSC", 2000, "Trường ĐH Kinh tế TP.HCM"),
            ("PHD", 2007, "University of Queensland"),
            ("NVSP", 1999, NVSP_ORG),
            ("LLCT", 2019, "Học viện Chính trị Quốc gia Hồ Chí Minh"),
        ],
    ),
    (
        "Lê Văn Hòa",
        "Male",
        "1975-02-18",
        None,
        "Active",
        [
            ("PDT", "CV", "2001-03-01", "2014-06-30", P),
            ("PDT", "TP", "2014-07-01", None, P),
        ],
        [("BA", 1997, SPDN), ("MSC", 2008, SPDN)],
    ),
    (
        "Võ Thị Thanh Tâm",
        "Female",
        "1980-11-07",
        None,
        "Active",
        [
            ("PTS", "CV", "2006-03-01", "2019-06-30", P),
            ("PTS", "TP", "2019-07-01", None, P),
        ],
        [("BA", 2002, "Trường ĐH Khoa học - ĐH Huế")],
    ),
    (
        "Đỗ Minh Khôi",
        "Male",
        "1978-06-25",
        None,
        "Active",
        [
            ("PTCNS", "CV", "2004-03-01", "2016-06-30", P),
            ("PTCNS", "TP", "2016-07-01", None, P),
        ],
        [("BA", 2000, LAWH), ("MSC", 2010, "Học viện Hành chính Quốc gia")],
    ),
    (
        "Bùi Thị Kim Oanh",
        "Female",
        "1979-01-30",
        None,
        "Active",
        [
            ("PKHTC", "CV", "2003-03-01", "2017-06-30", P),
            ("PKHTC", "TP", "2017-07-01", None, P),
        ],
        [("BA", 2001, KTDN), ("KTT", 2015, "Học viện Tài chính")],
    ),
    (
        "Ngô Thị Hồng Nhung",
        "Female",
        "1990-04-14",
        None,
        "Active",
        [("PDT", "CV", "2014-03-01", None, P)],
        [("BA", 2012, SPDN)],
    ),
    (
        "Phan Quốc Việt",
        "Male",
        "1992-12-02",
        None,
        "Active",
        [("PDT", "CV", "2017-03-01", None, P)],
        [("ENG", 2015, BKDN)],
    ),
    (
        "Huỳnh Thị Diễm",
        "Female",
        "1995-08-19",
        None,
        "Active",
        [("PTS", "CV", "2019-03-01", None, P)],
        [("BA", 2017, KTDN)],
    ),
    (
        "Trương Văn Lực",
        "Male",
        "1988-03-09",
        None,
        "Active",
        [
            ("PTCNS", "NV", "2012-03-01", "2018-06-30", P),
            ("PTCNS", "CV", "2018-07-01", None, P),
        ],
        [("BA", 2010, LAWH)],
    ),
    (
        "Lý Thị Ngọc Hà",
        "Female",
        "1993-10-11",
        None,
        "Resigned",
        [("PKHTC", "CV", "2016-03-01", "2024-12-31", P)],
        [("BA", 2015, KTDN)],
    ),
    (
        "Đinh Công Sơn",
        "Male",
        "1996-07-27",
        None,
        "Active",
        [("PTS", "NV", "2021-03-01", None, P)],
        [("BA", 2018, NNDN)],
    ),
    (
        "Đặng Quốc Bảo",
        "Male",
        "1978-03-21",
        ("Assoc. Prof.", "Trí tuệ nhân tạo"),
        "Active",
        [
            ("BMKHMT", "GV", "2003-08-15", "2012-06-30", P),
            ("BMKHMT", "GVC", "2012-07-01", "2018-06-30", P),
            ("KCNTT", "TK", "2018-07-01", None, P),
        ],
        [
            ("ENG", 2001, BKDN),
            ("MSC", 2005, "Trường ĐH Bách khoa Hà Nội"),
            ("PHD", 2010, "National Taiwan University"),
            ("NVSP", 2004, NVSP_ORG),
            ("IELTS", 2008, "British Council"),
        ],
    ),
    (
        "Bùi Thu Trang",
        "Female",
        "1986-07-02",
        ("Ph.D.", "Hệ thống thông tin"),
        "Active",
        [
            ("BMHTTT", "GV", "2010-08-15", "2019-06-30", P),
            ("BMHTTT", "TBM", "2019-07-01", None, P),
        ],
        [
            ("ENG", 2008, BKDN),
            ("MSC", 2012, BKDN),
            ("PHD", 2018, "University of Melbourne"),
            ("NVSP", 2011, NVSP_ORG),
            ("IELTS", 2014, "IDP Education"),
        ],
    ),
    (
        "Huỳnh Gia Khang",
        "Male",
        "1990-12-15",
        ("M.Sc.", "Mạng máy tính"),
        "Active",
        [("BMKHMT", "GV", "2015-08-15", None, P)],
        [
            ("ENG", 2013, BKDN),
            ("MSC", 2017, BKDN),
            ("NVSP", 2016, NVSP_ORG),
            ("AWS", 2022, "Amazon Web Services"),
        ],
    ),
    (
        "Ngô Khánh Linh",
        "Female",
        "1989-05-28",
        ("Ph.D.", "Khoa học dữ liệu"),
        "Active",
        [
            ("BMKHMT", "GV", "2013-08-15", "2021-06-30", P),
            ("BMKHMT", "TBM", "2021-07-01", None, P),
        ],
        [
            ("BA", 2011, "Trường ĐH Khoa học Tự nhiên - ĐHQG TP.HCM"),
            ("MSC", 2014, "Trường ĐH Khoa học Tự nhiên - ĐHQG TP.HCM"),
            ("PHD", 2020, "KAIST"),
            ("NVSP", 2014, NVSP_ORG),
            ("IELTS", 2015, "British Council"),
        ],
    ),
    (
        "Phan Hữu Nghĩa",
        "Male",
        "1984-09-09",
        ("M.Sc.", "Công nghệ phần mềm"),
        "Active",
        [
            ("BMHTTT", "GV", "2009-08-15", "2020-06-30", P),
            ("BMHTTT", "GVC", "2020-07-01", None, P),
        ],
        [("ENG", 2007, BKDN), ("MSC", 2011, BKDN), ("NVSP", 2010, NVSP_ORG)],
    ),
    (
        "Trịnh Minh Đức",
        "Male",
        "1987-01-17",
        ("Ph.D.", "An toàn thông tin"),
        "Resigned",
        [("BMKHMT", "GV", "2012-08-15", "2025-06-30", P)],
        [
            ("ENG", 2009, "Trường ĐH Bách khoa Hà Nội"),
            ("MSC", 2013, "Trường ĐH Bách khoa Hà Nội"),
            ("PHD", 2019, "Trường ĐH Bách khoa Hà Nội"),
            ("NVSP", 2013, NVSP_ORG),
        ],
    ),
    (
        "Mai Thị Thu Hà",
        "Female",
        "1994-06-06",
        ("M.Sc.", "Kỹ thuật phần mềm"),
        "Active",
        [("BMHTTT", "GV", "2020-08-15", None, P)],
        [("ENG", 2017, BKDN), ("MSC", 2019, BKDN), ("NVSP", 2021, NVSP_ORG)],
    ),
    (
        "Dương Minh Quân",
        "Male",
        "1979-10-04",
        ("Ph.D.", "Quản trị kinh doanh"),
        "Active",
        [
            ("KKT", "GV", "2004-08-15", "2013-06-30", P),
            ("KKT", "GVC", "2013-07-01", "2019-06-30", P),
            ("KKT", "TK", "2019-07-01", None, P),
        ],
        [
            ("BA", 2001, KTDN),
            ("MSC", 2006, "Trường ĐH Kinh tế TP.HCM"),
            ("PHD", 2012, "Trường ĐH Kinh tế Quốc dân"),
            ("NVSP", 2005, NVSP_ORG),
        ],
    ),
    (
        "Lê Thị Phương",
        "Female",
        "1987-02-26",
        ("M.Sc.", "Marketing"),
        "Active",
        [("KKT", "GV", "2011-08-15", None, P)],
        [("BA", 2009, KTDN), ("MSC", 2013, KTDN), ("NVSP", 2012, NVSP_ORG)],
    ),
    (
        "Trần Bảo Ngân",
        "Female",
        "1982-11-19",
        ("Ph.D.", "Kế toán - Kiểm toán"),
        "Active",
        [
            ("KKT", "GV", "2007-08-15", "2016-06-30", P),
            ("KKT", "GVC", "2016-07-01", "2022-06-30", P),
            ("KKT", "PTK", "2022-07-01", None, P),
        ],
        [
            ("BA", 2004, KTDN),
            ("MSC", 2009, "Học viện Tài chính"),
            ("PHD", 2015, "Học viện Tài chính"),
            ("NVSP", 2008, NVSP_ORG),
            ("KTT", 2011, "Học viện Tài chính"),
        ],
    ),
    (
        "Hà Văn Thắng",
        "Male",
        "1985-08-08",
        ("M.Sc.", "Tài chính doanh nghiệp"),
        "Active",
        [("KKT", "GV", "2012-08-15", None, P)],
        [
            ("BA", 2007, "Học viện Tài chính"),
            ("MSC", 2011, "Trường ĐH Kinh tế TP.HCM"),
            ("NVSP", 2013, NVSP_ORG),
        ],
    ),
    (
        "Nguyễn Thị Yến",
        "Female",
        "1981-04-03",
        ("Ph.D.", "Ngôn ngữ Anh"),
        "Active",
        [
            ("KNN", "GV", "2006-08-15", "2015-06-30", P),
            ("KNN", "GVC", "2015-07-01", "2020-06-30", P),
            ("KNN", "TK", "2020-07-01", None, P),
        ],
        [
            ("BA", 2003, NNDN),
            ("MSC", 2008, "University of Queensland"),
            ("PHD", 2014, "Trường ĐH KHXH&NV - ĐHQG TP.HCM"),
            ("NVSP", 2007, NVSP_ORG),
            ("IELTS", 2006, "British Council"),
        ],
    ),
    (
        "Hồ Minh Nhật",
        "Male",
        "1991-09-22",
        ("M.Sc.", "Biên - Phiên dịch"),
        "Active",
        [("KNN", "GV", "2016-08-15", None, P)],
        [
            ("BA", 2013, NNDN),
            ("MSC", 2016, NNDN),
            ("NVSP", 2017, NVSP_ORG),
            ("IELTS", 2013, "IDP Education"),
        ],
    ),
    (
        "Phạm Thị Lan",
        "Female",
        "1983-12-12",
        ("M.Sc.", "Giảng dạy tiếng Anh"),
        "Active",
        [("KNN", "GV", "2008-08-15", None, P)],
        [
            ("BA", 2005, NNDN),
            ("MSC", 2010, "Trường ĐH Ngoại ngữ - ĐHQG Hà Nội"),
            ("NVSP", 2009, NVSP_ORG),
            ("IELTS", 2011, "British Council"),
        ],
    ),
    (
        "Hoàng Ngọc Mai",
        "Female",
        "1988-07-30",
        ("M.Sc.", "Ngôn ngữ học ứng dụng"),
        "Active",
        [("KNN", "GV", "2013-08-15", None, P)],
        [
            ("BA", 2010, "Trường ĐH Ngoại ngữ - ĐH Huế"),
            ("MSC", 2013, "Trường ĐH Ngoại ngữ - ĐH Huế"),
            ("NVSP", 2014, NVSP_ORG),
            ("IELTS", 2012, "IDP Education"),
        ],
    ),
    (
        "Phạm Văn Dũng",
        "Male",
        "1963-10-20",
        ("Assoc. Prof.", "Kết cấu công trình"),
        "Retired",
        [
            ("KXD", "GV", "1990-08-15", "2000-06-30", P),
            ("KXD", "GVC", "2000-07-01", "2010-06-30", P),
            ("KXD", "TK", "2010-07-01", "2022-12-31", P),
            ("KXD", "GVC", "2023-01-01", "2025-01-31", P),
        ],
        [
            ("ENG", 1986, "Trường ĐH Xây dựng Hà Nội"),
            ("MSC", 1995, "Trường ĐH Xây dựng Hà Nội"),
            ("PHD", 2001, "Trường ĐH Xây dựng Hà Nội"),
            ("NVSP", 1991, NVSP_ORG),
        ],
    ),
    (
        "Tạ Quang Huy",
        "Male",
        "1976-05-16",
        ("Ph.D.", "Địa kỹ thuật công trình"),
        "Active",
        [
            ("KXD", "GV", "2002-08-15", "2011-06-30", P),
            ("KXD", "GVC", "2011-07-01", "2022-12-31", P),
            ("KXD", "TK", "2023-01-01", None, P),
        ],
        [
            ("ENG", 1999, BKDN),
            ("MSC", 2004, BKDN),
            ("PHD", 2010, BKDN),
            ("NVSP", 2003, NVSP_ORG),
        ],
    ),
    (
        "Đỗ Thành Long",
        "Male",
        "1986-03-05",
        ("M.Sc.", "Địa kỹ thuật"),
        "Active",
        [("KXD", "GV", "2012-08-15", None, P)],
        [("ENG", 2009, BKDN), ("MSC", 2014, BKDN), ("NVSP", 2013, NVSP_ORG)],
    ),
    (
        "Châu Thị Bích Ngọc",
        "Female",
        "1990-10-24",
        ("M.Sc.", "Vật liệu xây dựng"),
        "Active",
        [("KXD", "GV", "2016-08-15", None, P)],
        [
            ("ENG", 2013, "Trường ĐH Xây dựng Hà Nội"),
            ("MSC", 2016, "Trường ĐH Xây dựng Hà Nội"),
            ("NVSP", 2017, NVSP_ORG),
        ],
    ),
    (
        "Nguyễn Văn Thành",
        "Male",
        "1972-01-08",
        ("Ph.D.", "Triết học"),
        "Active",
        [
            ("KLLCT", "GV", "1998-08-15", "2016-06-30", P),
            ("KLLCT", "TK", "2016-07-01", None, P),
        ],
        [
            ("BA", 1994, "Trường ĐH KHXH&NV - ĐHQG Hà Nội"),
            ("MSC", 2001, "Trường ĐH KHXH&NV - ĐHQG Hà Nội"),
            ("PHD", 2009, "Học viện Chính trị Quốc gia Hồ Chí Minh"),
            ("NVSP", 1999, NVSP_ORG),
            ("LLCT", 2014, "Học viện Chính trị Quốc gia Hồ Chí Minh"),
        ],
    ),
    (
        "Trần Thị Hương",
        "Female",
        "1980-06-17",
        ("M.Sc.", "Lịch sử Đảng"),
        "Active",
        [("KLLCT", "GV", "2005-08-15", None, P)],
        [("BA", 2002, SP), ("MSC", 2009, SP), ("NVSP", 2006, NVSP_ORG)],
    ),
    (
        "Lê Minh Tâm",
        "Male",
        "1985-11-29",
        ("M.Sc.", "Luật học"),
        "Active",
        [("KLLCT", "GV", "2011-08-15", None, P)],
        [
            ("BA", 2007, LAWH),
            ("MSC", 2011, "Trường ĐH Luật TP.HCM"),
            ("NVSP", 2012, NVSP_ORG),
        ],
    ),
    (
        "Võ Đức Hải",
        "Male",
        "1975-08-11",
        ("Ph.D.", "Toán ứng dụng"),
        "Active",
        [
            ("KKHCB", "GV", "2000-08-15", "2012-06-30", P),
            ("KKHCB", "GVC", "2012-07-01", "2018-06-30", P),
            ("KKHCB", "TK", "2018-07-01", None, P),
        ],
        [
            ("BA", 1997, SPDN),
            ("MSC", 2002, "Trường ĐH Khoa học Tự nhiên - ĐHQG Hà Nội"),
            ("PHD", 2008, "Viện Toán học"),
            ("NVSP", 2001, NVSP_ORG),
        ],
    ),
    (
        "Kiều Thị Minh Thư",
        "Female",
        "1991-02-13",
        ("M.Sc.", "Xác suất thống kê"),
        "Active",
        [("KKHCB", "GV", "2017-08-15", None, P)],
        [
            ("BA", 2013, SPDN),
            ("MSC", 2016, "Trường ĐH Khoa học Tự nhiên - ĐHQG TP.HCM"),
            ("NVSP", 2017, NVSP_ORG),
        ],
    ),
]

employees, lecturers, emp_quals, assignments = [], [], [], []
used_emails = set()
for eid, (name, gender, dob, lect, status, career, quals) in enumerate(EMPLOYEES, 1):
    hire = min(a[2] for a in career)
    parts = ascii_slug(name).split()
    email = f"{parts[-1]}.{''.join(p[0] for p in parts[:-1])}@example.com"
    if email in used_emails:
        email = email.replace("@", f"{eid}@")
    used_emails.add(email)
    if rng.random() < 0.05:
        email = None  # nhân sự chưa cấp email
    phone = (
        None
        if rng.random() < 0.1
        else rng.choice(["09", "03", "07", "08"]) + (f"{rng.randrange(10**8):08d}")
    )
    employees.append(
        (eid, f"NV{eid:04d}", name, dob, gender, email, phone, hire, status)
    )
    if lect:
        lecturers.append((eid, *lect))
    for code, year, org in quals:
        emp_quals.append((eid, qual_id[code], org, year))
    for dept, pos, start, end, primary in career:
        assignments.append(
            (
                eid,
                dept_id[dept],
                pos_id[pos],
                start,
                end,
                "true" if primary else "false",
            )
        )

write(
    "hr/employee.csv",
    [
        "employee_id",
        "employee_code",
        "full_name",
        "date_of_birth",
        "gender",
        "email",
        "phone",
        "hire_date",
        "employee_status",
    ],
    employees,
)
write("hr/lecturer.csv", ["lecturer_id", "academic_rank", "research_field"], lecturers)
write(
    "hr/employee_qualification.csv",
    ["employee_id", "qualification_id", "institution", "year_obtained"],
    emp_quals,
)
assignments.sort(key=lambda a: (a[0], a[3], a[5] == "false"))
write(
    "hr/assignment.csv",
    [
        "assignment_id",
        "employee_id",
        "department_id",
        "position_id",
        "start_date",
        "end_date",
        "is_primary",
    ],
    [(i, *a) for i, a in enumerate(assignments, 1)],
)
assert all(date.fromisoformat(e[7]) <= date(2026, 9, 25) for e in employees)
print(
    len(DEPARTMENTS),
    len(POSITIONS),
    len(QUALIFICATIONS),
    len(employees),
    len(lecturers),
    len(emp_quals),
    len(assignments),
)
