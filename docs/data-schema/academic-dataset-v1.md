# Academic/Training Dataset Schema V1

| Mục | Giá trị |
|---|---|
| Domain | Academic/Training (Đào tạo) |
| Phiên bản tài liệu | 1.2.0 (cập nhật theo DB ngày 01/10/2026) |
| Nguồn chuẩn | [unilake-db-architecture.txt](unilake-db-architecture.txt): UniLake AI Database Architecture |
| Phạm vi | Theo proposal: Student, Program, Major, Course, Class, Semester, Grade; DB thêm Curriculum, Curriculum Course, Class Lecturer, Enrollment |
| Dùng cho | Data Ingestion (W3), lớp Bronze → Silver → Gold, NLQ |
| Liên quan | [Admissions Dataset V1](admissions-dataset-v1.md): dùng chung `program`, `major`, `admission_year`; `student` trỏ tới `applicant`, `admission`. [HR Dataset V1](hr-dataset-v1.md): `lecturer` |

Các file đi kèm:

| File | Vai trò | Trạng thái |
|---|---|---|
| [unilake-db-architecture.txt](unilake-db-architecture.txt) | DB nguồn toàn hệ thống | Nguồn chuẩn |
| [academic_v1.schema.json](academic_v1.schema.json) | Schema máy đọc: kiểu DB và kiểu SQL, null, unique, FK, thang điểm, cột dẫn xuất ở Silver, luật DQ | Khớp DB (có test) |
| [academic_v1.sql](academic_v1.sql) | DDL PostgreSQL cho 9 bảng Academic; chạy sau `hr_v1.sql` và `admissions_v1.sql` | Khớp DB (có test) |
| [backend/tests/test_dataset_schemas.py](../../backend/tests/test_dataset_schemas.py) | Đối chiếu JSON với DB, DDL với JSON, và kiểm tra CSV mẫu theo JSON | Đã cập nhật |
| `data/samples/training/*.csv` | Dữ liệu mẫu | Sinh theo DB cũ, cần sinh lại (mục 9) |
| [data/synthetic/generate_academic.py](../../data/synthetic/generate_academic.py) | Sinh dữ liệu mẫu | Cần cập nhật theo DB mới |
| [backend/tests/test_academic_dataset.py](../../backend/tests/test_academic_dataset.py) | Kiểm tra luật nghiệp vụ | Cần cập nhật theo DB mới |

## 1. Entity và phân loại

| Bảng | Vai trò | Thuộc dataset |
|---|---|---|
| `student` | Sinh viên | Academic |
| `course` | Học phần | Academic |
| `curriculum` | Chương trình khung (một phiên bản kế hoạch đào tạo của một chương trình) | Academic |
| `curriculum_course` | Học phần trong chương trình khung: học kỳ gợi ý, loại, bắt buộc, tiên quyết | Academic |
| `semester` | Học kỳ (HK1, HK2, học kỳ hè) | Academic |
| `class` | Lớp học phần: học phần mở trong một học kỳ | Academic |
| `class_lecturer` | Giảng viên phụ trách lớp (có thể nhiều người, nhiều vai trò) | Academic |
| `enrollment` | Đăng ký lớp học phần (sinh viên × lớp) | Academic |
| `enrollment_grade` | Kết quả học tập của một lượt đăng ký lớp (Grade trong proposal) | Academic |
| `program`, `major`, `admission_year` | Chương trình, ngành, khóa | Admissions (dùng chung) |
| `applicant`, `admission` | Thí sinh và hồ sơ trúng tuyển mà sinh viên được tạo từ đó | Admissions |
| `lecturer`, `employee` | Giảng viên | HR |

Trong DB, `student` nằm trong nhóm màu Admissions. Tài liệu vẫn đặc tả `student` ở Academic theo proposal (Academic gồm Student); quan hệ với tuyển sinh thể hiện qua `student.admission_id`, `student.applicant_id`.

## 2. Quy ước đặt tên

Giống Admissions (xem [admissions-dataset-v1.md mục 1](admissions-dataset-v1.md#1-quy-ước)), kể cả cách đọc các cột Kiểu DB, Kiểu SQL, Null. Thêm:

| Đối tượng | Quy ước | Ví dụ |
|---|---|---|
| `semester_code` | `<năm bắt đầu năm học>-<1/2/3>`: 1 = HK1, 2 = HK2, 3 = học kỳ hè | `2025-2` = HK2 năm học 2025-2026 |
| `academic_year` | `YYYY-YYYY`, trùng định dạng `admission_year.year_label` | `2025-2026` |
| `course_code` | 2-4 chữ in hoa (nhóm môn) + 3 số (chữ số đầu = năm học gợi ý) | `CS201`, `GEN101` |
| `curriculum_code` | `<program_code>-<năm áp dụng>` | `CNTT-CQ-2024` |
| `class_code` | `<course_code>-<semester_code>-<nhóm 2 số>` | `CS201-2025-1-01` |
| `student_code` | năm nhập học (4) + mã chương trình (2) + số thứ tự (4) | `2025010007` |
| Điểm | `midterm_score`, `final_score`, `total_score` thang 10; `grade_value` hệ 4; điểm chữ `A`, `B+`, `B`, `C+`, `C`, `D+`, `D`, `F` | `7.30`, `3.00`, `B` |

## 3. Sơ đồ schema

```mermaid
erDiagram
    APPLICANT |o--o| STUDENT : "trở thành"
    ADMISSION |o--o| STUDENT : "nhập học từ"
    PROGRAM ||--o{ STUDENT : "theo học"
    ADMISSION_YEAR ||--o{ STUDENT : "khóa"
    PROGRAM ||--o{ CURRICULUM : "có phiên bản"
    CURRICULUM ||--o{ CURRICULUM_COURSE : "gồm"
    COURSE ||--o{ CURRICULUM_COURSE : "thuộc"
    COURSE |o--o{ CURRICULUM_COURSE : "là tiên quyết"
    COURSE ||--o{ CLASS : "mở thành"
    SEMESTER ||--o{ CLASS : "tổ chức trong"
    CLASS ||--o{ CLASS_LECTURER : "do"
    LECTURER ||--o{ CLASS_LECTURER : "phụ trách"
    STUDENT ||--o{ ENROLLMENT : "đăng ký"
    CLASS ||--o{ ENROLLMENT : "có"
    ENROLLMENT ||--o| ENROLLMENT_GRADE : "có kết quả"

    DEPARTMENT {
        bigint department_id PK
        bigint parent_department_id FK
    }
    EMPLOYEE {
        bigint employee_id PK
    }
    LECTURER {
        bigint employee_id PK, FK
    }
    MAJOR {
        bigint major_id PK
        bigint department_id FK
    }
    PROGRAM {
        bigint program_id PK
        bigint major_id FK
    }
    ADMISSION_METHOD {
        bigint admission_method_id PK
    }
    ADMISSION_YEAR {
        bigint admission_year_id PK
    }
    APPLICANT {
        bigint applicant_id PK
    }
    ADMISSION {
        bigint admission_id PK
        bigint applicant_id FK
        bigint program_id FK
        bigint admission_method_id FK
        bigint admission_year_id FK
    }
    STUDENT {
        bigint student_id PK
        string student_code UK
        bigint applicant_id FK, UK
        bigint admission_id FK, UK
        string full_name
        date date_of_birth
        string gender
        bigint program_id FK
        bigint admission_year_id FK
        string student_status
        timestamp created_at
    }
    COURSE {
        bigint course_id PK
        string course_code UK
        string course_name
        int credit_hours
        string status
    }
    CURRICULUM {
        bigint curriculum_id PK
        bigint program_id FK
        string curriculum_code
        string curriculum_name
        string version
        date effective_from
        date effective_to
        string status
    }
    CURRICULUM_COURSE {
        bigint curriculum_course_id PK
        bigint curriculum_id FK
        bigint course_id FK
        int semester_no
        string course_type
        boolean is_required
        bigint prerequisite_course_id FK
    }
    SEMESTER {
        bigint semester_id PK
        string semester_code UK
        string academic_year
        date start_date
        date end_date
        string status
    }
    CLASS {
        bigint class_id PK
        string class_code UK
        bigint course_id FK
        bigint semester_id FK
        int max_capacity
        string room
        string class_status
    }
    CLASS_LECTURER {
        bigint class_lecturer_id PK
        bigint class_id FK
        bigint lecturer_id FK
        string teaching_role
        boolean is_primary
        date assigned_from
        date assigned_to
    }
    ENROLLMENT {
        bigint enrollment_id PK
        bigint student_id FK
        bigint class_id FK
        date enrollment_date
        string enrollment_status
        timestamp created_at
    }
    ENROLLMENT_GRADE {
        bigint enrollment_grade_id PK
        bigint enrollment_id FK, UK
        decimal midterm_score
        decimal final_score
        decimal total_score
        decimal grade_value
        string letter_grade
        date registration_date
    }
```

Chuỗi quan hệ chính: **Student → Program → Major**; **Program → Curriculum → Curriculum Course → Course**; **Course → Class → Semester**; **Student → Enrollment → Class**, **Enrollment → Enrollment Grade**; **Class → Class Lecturer → Lecturer**.

| Quan hệ | Bản số | Ghi chú |
|---|---|---|
| program → student | 1 - N | Sinh viên thuộc đúng một chương trình; ngành lấy qua `program.major_id` |
| admission_year → student | 1 - N | Khóa của sinh viên (khóa 2025 = `2025-2026`) |
| admission → student, applicant → student | 1 - 0..1 | Cả hai được để trống với sinh viên nhập từ hệ thống cũ |
| program → curriculum | 1 - N | Mỗi chương trình có nhiều phiên bản khung theo thời gian |
| curriculum ↔ course | N - N qua `curriculum_course` | Mỗi học phần một lần trong một khung |
| course → curriculum_course (tiên quyết) | 0..1 - N | `prerequisite_course_id`; mỗi dòng chỉ ghi được một học phần tiên quyết |
| course → class, semester → class | 1 - N | Một học phần có thể mở nhiều nhóm trong một học kỳ |
| class ↔ lecturer | N - N qua `class_lecturer` | Một lớp có giảng viên chính và có thể có trợ giảng |
| student ↔ class | N - N qua `enrollment` | Mỗi sinh viên đăng ký một lớp một lần |
| enrollment → enrollment_grade | 1 - 0..1 | `enrollment_id` UNIQUE ở `enrollment_grade` |

## 4. Đặc tả bảng

### 4.1 `student` – Sinh viên

| Cột | Kiểu DB | Kiểu SQL | Null | Khóa | Ràng buộc / mô tả |
|---|---|---|---|---|---|
| student_id | bigint | bigint | N | PK |  |
| student_code | string | varchar(30) | N | UK | Năm nhập học (4) + mã chương trình (2) + số thứ tự (4); `^[0-9]{10}$` |
| applicant_id | bigint | bigint | Y | FK → applicant, UK |  |
| admission_id | bigint | bigint | Y | FK → admission, UK | Hồ sơ trúng tuyển dẫn tới nhập học |
| full_name | string | varchar(200) | N |  | **PII** |
| date_of_birth | date | date | Y |  | **PII** |
| gender | string | varchar(20) | Y |  | `Male`, `Female` |
| program_id | bigint | bigint | N | FK → program |  |
| admission_year_id | bigint | bigint | N | FK → admission_year | Khóa (cohort) |
| student_status | string | varchar(30) | N |  | `Active`, `Graduated`, `Suspended`, `Dropped` |
| created_at | timestamp | timestamp | N |  |  |

### 4.2 `course` – Học phần

| Cột | Kiểu DB | Kiểu SQL | Null | Khóa | Ràng buộc / mô tả |
|---|---|---|---|---|---|
| course_id | bigint | bigint | N | PK |  |
| course_code | string | varchar(30) | N | UK | `^[A-Z]{2,4}[0-9]{3}$` |
| course_name | string | varchar(200) | N |  |  |
| credit_hours | int | int | N |  | Số tín chỉ; 1 - 10 |
| status | string | varchar(20) | N |  | `Active`, `Inactive` |

DB không có `course.major_id`: học phần thuộc ngành nào được suy ra qua `curriculum_course` → `curriculum` → `program` → `major`.

### 4.3 `curriculum` – Chương trình khung

| Cột | Kiểu DB | Kiểu SQL | Null | Khóa | Ràng buộc / mô tả |
|---|---|---|---|---|---|
| curriculum_id | bigint | bigint | N | PK |  |
| program_id | bigint | bigint | N | FK → program |  |
| curriculum_code | string | varchar(50) | N |  | <program_code>-<năm áp dụng>; `^[A-Z]+-[A-Z]+-[0-9]{4}$` |
| curriculum_name | string | varchar(250) | N |  |  |
| version | string | varchar(30) | N |  |  |
| effective_from | date | date | Y |  |  |
| effective_to | date | date | Y |  | Null = đang áp dụng |
| status | string | varchar(20) | N |  | `Draft`, `Active`, `Retired` |

UNIQUE đề xuất (chưa có trong DB, đã đưa vào DDL): `(curriculum_code)`; `(program_id, version)`. Mỗi chương trình tối đa một khung `Active` tại một thời điểm. Sinh viên học theo khung có hiệu lực vào năm nhập học.

### 4.4 `curriculum_course` – Học phần trong chương trình khung

| Cột | Kiểu DB | Kiểu SQL | Null | Khóa | Ràng buộc / mô tả |
|---|---|---|---|---|---|
| curriculum_course_id | bigint | bigint | N | PK |  |
| curriculum_id | bigint | bigint | N | FK → curriculum |  |
| course_id | bigint | bigint | N | FK → course |  |
| semester_no | int | int | Y |  | Học kỳ gợi ý; 1 - 12 |
| course_type | string | varchar(30) | Y |  | `General`, `Foundation`, `Major`, `Elective` |
| is_required | boolean | boolean | N |  |  |
| prerequisite_course_id | bigint | bigint | Y | FK → course |  |

UNIQUE đề xuất (chưa có trong DB, đã đưa vào DDL): `(curriculum_id, course_id)`. Học phần tiên quyết phải khác chính nó, có trong cùng khung và có `semester_no` nhỏ hơn. `course_type = Elective` thì `is_required = false`.

### 4.5 `semester` – Học kỳ

| Cột | Kiểu DB | Kiểu SQL | Null | Khóa | Ràng buộc / mô tả |
|---|---|---|---|---|---|
| semester_id | bigint | bigint | N | PK |  |
| semester_code | string | varchar(30) | N | UK | `^[0-9]{4}-[1-3]$` |
| academic_year | string | varchar(20) | N |  | Khớp admission_year.year_label; `^[0-9]{4}-[0-9]{4}$` |
| start_date | date | date | N |  |  |
| end_date | date | date | N |  |  |
| status | string | varchar(20) | N |  | `Planned`, `Ongoing`, `Completed` |

### 4.6 `class` – Lớp học phần

| Cột | Kiểu DB | Kiểu SQL | Null | Khóa | Ràng buộc / mô tả |
|---|---|---|---|---|---|
| class_id | bigint | bigint | N | PK |  |
| class_code | string | varchar(50) | N | UK | <course_code>-<semester_code>-<nhóm>; `^[A-Z]{2,4}[0-9]{3}-[0-9]{4}-[1-3]-[0-9]{2}$` |
| course_id | bigint | bigint | N | FK → course |  |
| semester_id | bigint | bigint | N | FK → semester |  |
| max_capacity | int | int | Y |  | Null = không giới hạn/chưa chốt; 1 - 200 |
| room | string | varchar(50) | Y |  | Null = chưa xếp phòng hoặc học trực tuyến |
| class_status | string | varchar(30) | N |  | `Open`, `InProgress`, `Completed`, `Cancelled` |

Giảng viên của lớp nằm ở `class_lecturer` (DB không còn `class.lecturer_id`).

### 4.7 `class_lecturer` – Giảng viên của lớp

| Cột | Kiểu DB | Kiểu SQL | Null | Khóa | Ràng buộc / mô tả |
|---|---|---|---|---|---|
| class_lecturer_id | bigint | bigint | N | PK |  |
| class_id | bigint | bigint | N | FK → class |  |
| lecturer_id | bigint | bigint | N | FK → lecturer.employee_id |  |
| teaching_role | string | varchar(50) | Y |  | `Lecturer`, `Assistant`, `Lab` |
| is_primary | boolean | boolean | N |  | Mỗi lớp đúng một dòng true |
| assigned_from | date | date | Y |  | Null = từ đầu học kỳ |
| assigned_to | date | date | Y |  | Null = tới hết học kỳ |

UNIQUE đề xuất (chưa có trong DB, đã đưa vào DDL): `(class_id, lecturer_id)`. Mỗi lớp đúng một dòng `is_primary = true` (unique index một phần trong DDL).

### 4.8 `enrollment` – Đăng ký lớp học phần

| Cột | Kiểu DB | Kiểu SQL | Null | Khóa | Ràng buộc / mô tả |
|---|---|---|---|---|---|
| enrollment_id | bigint | bigint | N | PK |  |
| student_id | bigint | bigint | N | FK → student |  |
| class_id | bigint | bigint | N | FK → class |  |
| enrollment_date | date | date | N |  |  |
| enrollment_status | string | varchar(30) | N |  | `Registered`, `Withdrawn`, `Completed` |
| created_at | timestamp | timestamp | N |  |  |

UNIQUE đề xuất (chưa có trong DB, đã đưa vào DDL): `(student_id, class_id)`.

### 4.9 `enrollment_grade` – Kết quả học tập

| Cột | Kiểu DB | Kiểu SQL | Null | Khóa | Ràng buộc / mô tả |
|---|---|---|---|---|---|
| enrollment_grade_id | bigint | bigint | N | PK |  |
| enrollment_id | bigint | bigint | N | FK → enrollment, UK |  |
| midterm_score | decimal | decimal(5,2) | Y |  | 0 - 10 |
| final_score | decimal | decimal(5,2) | Y |  | 0 - 10 |
| total_score | decimal | decimal(5,2) | Y |  | round(0.3 x midterm + 0.7 x final, 1); 0 - 10 |
| grade_value | decimal | decimal(4,2) | Y |  | Điểm hệ 4 quy đổi từ total_score; 0 - 4 |
| letter_grade | string | varchar(5) | Y |  | `A`, `B+`, `B`, `C+`, `C`, `D+`, `D`, `F` |
| registration_date | date | date | Y |  | Khi có thì bằng enrollment.enrollment_date |

Các cột điểm để trống khi học kỳ chưa kết thúc (sinh viên đang học). `registration_date` được để trống; khi có thì phải bằng `enrollment.enrollment_date`.

## 5. Quy đổi điểm

Lưu trong `grading` của schema JSON, để ingestion và Gold dùng chung một nguồn. `total_score = round(0.3 × midterm_score + 0.7 × final_score, 1)`.

| `total_score` (thang 10) | `letter_grade` | `grade_value` (hệ 4) | Kết quả |
|---|---|---|---|
| 8.5 - 10 | A | 4.0 | Đạt |
| 8 - 8.4 | B+ | 3.5 | Đạt |
| 7 - 7.9 | B | 3.0 | Đạt |
| 6.5 - 6.9 | C+ | 2.5 | Đạt |
| 5.5 - 6.4 | C | 2.0 | Đạt |
| 5 - 5.4 | D+ | 1.5 | Đạt |
| 4 - 4.9 | D | 1.0 | Đạt |
| < 4 | F | 0.0 | Không đạt, phải học lại |

## 6. Field phục vụ truy vấn học tập

DB nguồn không lưu GPA hay số tín chỉ tích lũy; lớp Silver dẫn xuất các cột sau (khai báo trong `silver_derived_columns`):

| Bảng Silver | Cột dẫn xuất | Kiểu | Cách tính |
|---|---|---|---|
| enrollment_grade | `is_passed` | boolean | letter_grade <> 'F'; null khi chưa có điểm |
| enrollment_grade | `credits_earned` | int | course.credit_hours nếu is_passed, ngược lại 0 |
| enrollment_grade | `attempt_no` | int | Thứ tự lần học cùng course_id của sinh viên, theo semester.start_date |
| enrollment_grade | `is_latest_attempt` | boolean | Lần học cuối cùng của sinh viên với course_id; GPA tích lũy chỉ tính lần này |
| enrollment_grade | `course_type` | varchar(30) | Từ curriculum_course của khung sinh viên đang học |
| semester | `semester_type` | varchar(10) | Hậu tố semester_code: 1 = HK1, 2 = HK2, 3 = Summer |
| student | `cohort_year` | int | Năm bắt đầu của admission_year.year_label |
| student | `major_id` | bigint | program.major_id |
| student | `curriculum_id` | bigint | Khung của program có hiệu lực vào năm nhập học |
| student | `birth_year` | int | Thay date_of_birth (PII) |
| class | `primary_lecturer_id` | bigint | class_lecturer có is_primary = true |
| class | `enrolled_count` | int | Số enrollment Registered hoặc Completed |

Câu hỏi NLQ/Dashboard mẫu và field dùng:

| Câu hỏi | Field |
|---|---|
| GPA học kỳ của sinh viên / của khóa / của ngành | `grade_value`, `credit_hours`, `semester_code`, `cohort_year`, `major_id` |
| GPA tích lũy, số tín chỉ tích lũy | `grade_value`, `credits_earned`, `is_latest_attempt` |
| Tiến độ học so với chương trình khung | `curriculum_course.semester_no`, `is_required`, `credits_earned` |
| Tỷ lệ đạt / trượt theo học phần, học kỳ, giảng viên | `is_passed`, `course_code`, `semester_code`, `primary_lecturer_id` |
| Sinh viên chưa đủ điều kiện tiên quyết | `prerequisite_course_id`, `is_passed` |
| Phân bố điểm chữ của một học phần | `letter_grade`, `course_id` |
| Số sinh viên theo trạng thái, khóa, chương trình | `student_status`, `cohort_year`, `program_id` |
| Tỷ lệ học lại, tỷ lệ lấp đầy lớp | `attempt_no > 1`; `enrolled_count` / `max_capacity` |
| Điểm đầu vào so với GPA năm nhất | `student.admission_id` → `admission.admission_score`, `admission_method.max_score` |

Gợi ý data mart Gold: `student_semester_gpa` (sinh viên × học kỳ), `course_semester_result` (học phần × học kỳ: số đăng ký, tỷ lệ đạt, điểm trung bình), `curriculum_progress` (sinh viên × khung: tín chỉ bắt buộc đã đạt / còn thiếu), `cohort_progress` (khóa × chương trình × trạng thái).

## 7. Luật chất lượng dữ liệu (DQ)

| ID | Bảng | Luật | Mức |
|---|---|---|---|
| ACA-DQ-01 | tất cả | PK và cột unique không null, không trùng | error |
| ACA-DQ-02 | tất cả | FK trỏ tới bản ghi có thật | error |
| ACA-DQ-03 | tất cả | Đúng kiểu, độ dài, null, giá trị trạng thái, pattern, min/max | error |
| ACA-DQ-04 | semester | end_date > start_date; không chồng lấn; academic_year khớp semester_code | error |
| ACA-DQ-05 | curriculum | Không trùng curriculum_code, (program_id, version); effective_to > effective_from khi có cả hai; tối đa một khung Active mỗi chương trình | error |
| ACA-DQ-06 | curriculum_course | Không trùng (curriculum_id, course_id); tiên quyết khác chính nó, có trong cùng khung, semester_no nhỏ hơn; course_type Elective thì is_required = false | error |
| ACA-DQ-07 | class_lecturer | Không trùng (class_id, lecturer_id); mỗi lớp đúng một giảng viên chính; assigned_from/assigned_to nằm trong học kỳ của lớp | error |
| ACA-DQ-08 | enrollment | Không trùng (student_id, class_id); không đăng ký cùng học phần hai lần trong một học kỳ; không học trước năm nhập học; enrollment_date <= semester.end_date | error |
| ACA-DQ-09 | class | Khi có max_capacity: số enrollment Registered/Completed <= max_capacity | error |
| ACA-DQ-10 | enrollment_grade | total_score đúng công thức; grade_value, letter_grade đúng bảng quy đổi | error |
| ACA-DQ-11 | enrollment_grade | Học kỳ đã xong thì đủ điểm; đang học thì chưa có điểm; registration_date (nếu có) = enrollment.enrollment_date | warning |
| ACA-DQ-12 | enrollment | Học phần đăng ký có trong khung của sinh viên (trừ học lại, tự chọn tự do) | warning |
| ACA-DQ-13 | student | Active thì có đăng ký ở học kỳ hiện tại; Dropped/Suspended thì không | warning |

## 8. Hướng dẫn ingestion (W3)

**Thứ tự nạp:** `department` → `employee` → `lecturer` → `major` → `program` → `admission_method` → `admission_year` → `applicant` → `admission` → `student` → `course` → `curriculum` → `curriculum_course` → `semester` → `class` → `class_lecturer` → `enrollment` → `enrollment_grade`. Các bảng HR và Admissions nạp trước vì Academic trỏ tới chúng.

**Bronze:** `bronze/academic/<table>/ingest_date=YYYY-MM-DD/<batch_id>.csv`.

**Bronze → Silver:**

1. Kiểm tra header theo `academic_v1.schema.json`.
2. Ép kiểu, kiểm tra null, giá trị trạng thái, pattern, range (DQ-01..03).
3. Kiểm tra FK và luật nghiệp vụ (DQ-02, DQ-04..13).
4. Tính cột dẫn xuất (mục 6); bỏ hoặc che PII của `student` như Admissions (mục 5 tài liệu Admissions).
5. Upsert theo PK nguồn. `enrollment_grade` thay đổi trong học kỳ (điểm giữa kỳ, cuối kỳ) nên nạp lại theo học kỳ đang mở.
6. Gửi kết quả DQ và lineage cho `governance`.

## 9. Dữ liệu mẫu

Dữ liệu mẫu hiện có trong `data/samples/training/` **được sinh theo DB cũ** (`unilake-db.dbml`): 63 sinh viên, 7 học kỳ (2024-1 → 2026-1), 62 học phần, 111 lớp, 705 lượt đăng ký có điểm; ngày chốt 25/09/2026. Khi sinh lại theo DB mới cần:

| Bảng | Thay đổi |
|---|---|
| `student` | Thêm `admission_id` (hồ sơ `Accepted`), `created_at` |
| `course` | Bỏ `major_id`, thêm `status` |
| `curriculum`, `curriculum_course` | Sinh mới từ chương trình khung đang viết cứng trong script: mỗi chương trình đại học một khung `Active` từ 2024, học phần theo `semester_no` 1 - 5, học phần chung là `General`, có quan hệ tiên quyết (ví dụ `CS101` → `CS102`) |
| `semester` | Thêm `status` |
| `class`, `class_lecturer` | Bỏ `class.lecturer_id`, thêm `class_status`; mỗi lớp một dòng `class_lecturer` giảng viên chính, một số lớp thêm trợ giảng |
| `enrollment`, `enrollment_grade` | Tách mỗi dòng `enrollment_grade` cũ thành một `enrollment` (sinh viên × lớp) và một `enrollment_grade` có PK riêng; thêm `total_score` (thang 10); `grade_value` đổi sang hệ 4 |

Logic sinh điểm giữ nguyên: điểm phụ thuộc năng lực sinh viên (tương quan với điểm xét tuyển) và độ khó học phần; F được học lại ở học kỳ hè; sinh viên `Dropped` dừng sau học kỳ 1 hoặc 2, `Suspended` không đăng ký học kỳ hiện tại.

## 10. Các điểm trong DB cần team xem

1. **Kiểu dữ liệu chỉ ở dạng chung.** Độ dài `varchar` và độ chính xác `decimal` trong cột "Kiểu SQL" là đề xuất.
2. **Chưa có UNIQUE nhiều cột.** `(curriculum_id, course_id)`, `(student_id, class_id)`, `(class_id, lecturer_id)`, `curriculum_code`, `(program_id, version)` hiện chỉ có trong DDL đề xuất; DB cũng chưa ràng buộc "một giảng viên chính mỗi lớp", "một khung Active mỗi chương trình".
3. **Chỉ một học phần tiên quyết.** `curriculum_course.prerequisite_course_id` chỉ ghi được một học phần; học phần có hai tiên quyết trở lên cần bảng `course_prerequisite(course_id, prerequisite_course_id)`.
4. **`course` không có khoa phụ trách.** Học phần dùng chung nhiều chương trình nên câu hỏi "tỷ lệ đạt theo khoa phụ trách học phần" phải đi qua `class_lecturer` → `assignment` (HR). Cân nhắc thêm `course.department_id`.
5. **`grade_value` và `total_score`.** Tài liệu quy ước `total_score` thang 10, `grade_value` hệ 4; tên cột chưa nói rõ thang, cần team xác nhận.
6. **`enrollment_grade.registration_date` trùng `enrollment.enrollment_date`.** DB đã cho cột này được để trống; nên bỏ hẳn.
7. **Nhiều cột chuyển sang được để trống.** `class.max_capacity`, `class_lecturer.assigned_from`, `curriculum.effective_from`, `curriculum_course.semester_no`, `course_type`: các luật sức chứa, thời gian phụ trách, hiệu lực khung, tiên quyết theo học kỳ chỉ áp dụng khi có dữ liệu.
8. **`semester.academic_year` là chuỗi, không phải FK.** Nối với khóa qua chuỗi `YYYY-YYYY` = `admission_year.year_label`, dễ lệch định dạng.
9. **Không có trọng số điểm, `class` không gắn chương trình.** Tỷ lệ 30/70 là quy ước chung; không phân biệt được lớp CLC với lớp đại trà nếu cùng học phần.
10. **`student` lặp PII và thông tin hồ sơ** (`applicant_id`, `program_id`, `admission_year_id` so với `admission`), đã ghi ở tài liệu Admissions (ADM-DQ-11, ADM-DQ-12).
