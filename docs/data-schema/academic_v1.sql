-- UniLake AI V1.0 - Schema dataset Đào tạo V1
-- DDL PostgreSQL cho các bảng Academic, bám đúng docs/data-schema/unilake-db-architecture.txt:
-- tên cột, thứ tự, NULL/NOT NULL, PK, UNIQUE, FK lấy từ DB; độ dài varchar/decimal và CHECK
-- là đề xuất của dataset (DB chỉ khai báo kiểu chung string/decimal). Xem academic_v1.schema.json.
-- Chạy sau hr_v1.sql (lecturer) và admissions_v1.sql (program, admission_year, applicant, admission).

CREATE TABLE IF NOT EXISTS student (
    student_id          BIGSERIAL    PRIMARY KEY,
    student_code        VARCHAR(30)  NOT NULL UNIQUE,
    applicant_id        BIGINT       UNIQUE REFERENCES applicant (applicant_id),
    admission_id        BIGINT       UNIQUE REFERENCES admission (admission_id),
    full_name           VARCHAR(200) NOT NULL,
    date_of_birth       DATE,
    gender              VARCHAR(20)  CHECK (gender IN ('Male', 'Female')),
    program_id          BIGINT       NOT NULL REFERENCES program (program_id),
    admission_year_id   BIGINT       NOT NULL REFERENCES admission_year (admission_year_id),
    student_status      VARCHAR(30)  NOT NULL
        CHECK (student_status IN ('Active', 'Graduated', 'Suspended', 'Dropped')),
    created_at          TIMESTAMP    NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS course (
    course_id           BIGSERIAL    PRIMARY KEY,
    course_code         VARCHAR(30)  NOT NULL UNIQUE,
    course_name         VARCHAR(200) NOT NULL,
    credit_hours        INT          NOT NULL CHECK (credit_hours BETWEEN 1 AND 10),
    status              VARCHAR(20)  NOT NULL DEFAULT 'Active' CHECK (status IN ('Active', 'Inactive'))
);

CREATE TABLE IF NOT EXISTS curriculum (
    curriculum_id       BIGSERIAL    PRIMARY KEY,
    program_id          BIGINT       NOT NULL REFERENCES program (program_id),
    curriculum_code     VARCHAR(50)  NOT NULL,
    curriculum_name     VARCHAR(250) NOT NULL,
    version             VARCHAR(30)  NOT NULL,
    effective_from      DATE,
    effective_to        DATE,
    status              VARCHAR(20)  NOT NULL CHECK (status IN ('Draft', 'Active', 'Retired')),
    UNIQUE (curriculum_code),
    UNIQUE (program_id, version),
    CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to > effective_from)
);

CREATE TABLE IF NOT EXISTS curriculum_course (
    curriculum_course_id    BIGSERIAL    PRIMARY KEY,
    curriculum_id           BIGINT       NOT NULL REFERENCES curriculum (curriculum_id),
    course_id               BIGINT       NOT NULL REFERENCES course (course_id),
    semester_no             INT          CHECK (semester_no BETWEEN 1 AND 12),
    course_type             VARCHAR(30)  CHECK (course_type IN ('General', 'Foundation', 'Major', 'Elective')),
    is_required             BOOLEAN      NOT NULL DEFAULT TRUE,
    prerequisite_course_id  BIGINT       REFERENCES course (course_id),
    UNIQUE (curriculum_id, course_id),
    CHECK (prerequisite_course_id IS DISTINCT FROM course_id),
    CHECK (course_type IS DISTINCT FROM 'Elective' OR NOT is_required)
);

CREATE TABLE IF NOT EXISTS semester (
    semester_id         BIGSERIAL    PRIMARY KEY,
    semester_code       VARCHAR(30)  NOT NULL UNIQUE CHECK (semester_code ~ '^[0-9]{4}-[1-3]$'),
    academic_year       VARCHAR(20)  NOT NULL CHECK (academic_year ~ '^[0-9]{4}-[0-9]{4}$'),
    start_date          DATE         NOT NULL,
    end_date            DATE         NOT NULL,
    status              VARCHAR(20)  NOT NULL CHECK (status IN ('Planned', 'Ongoing', 'Completed')),
    CHECK (end_date > start_date)
);

CREATE TABLE IF NOT EXISTS class (
    class_id            BIGSERIAL    PRIMARY KEY,
    class_code          VARCHAR(50)  NOT NULL UNIQUE,
    course_id           BIGINT       NOT NULL REFERENCES course (course_id),
    semester_id         BIGINT       NOT NULL REFERENCES semester (semester_id),
    max_capacity        INT          CHECK (max_capacity BETWEEN 1 AND 200),
    room                VARCHAR(50),
    class_status        VARCHAR(30)  NOT NULL
        CHECK (class_status IN ('Open', 'InProgress', 'Completed', 'Cancelled'))
);

CREATE TABLE IF NOT EXISTS class_lecturer (
    class_lecturer_id   BIGSERIAL    PRIMARY KEY,
    class_id            BIGINT       NOT NULL REFERENCES class (class_id),
    lecturer_id         BIGINT       NOT NULL REFERENCES lecturer (employee_id),
    teaching_role       VARCHAR(50)  CHECK (teaching_role IN ('Lecturer', 'Assistant', 'Lab')),
    is_primary          BOOLEAN      NOT NULL DEFAULT FALSE,
    assigned_from       DATE,
    assigned_to         DATE,
    UNIQUE (class_id, lecturer_id),
    CHECK (assigned_to IS NULL OR assigned_from IS NULL OR assigned_to >= assigned_from)
);

CREATE TABLE IF NOT EXISTS enrollment (
    enrollment_id       BIGSERIAL    PRIMARY KEY,
    student_id          BIGINT       NOT NULL REFERENCES student (student_id),
    class_id            BIGINT       NOT NULL REFERENCES class (class_id),
    enrollment_date     DATE         NOT NULL,
    enrollment_status   VARCHAR(30)  NOT NULL
        CHECK (enrollment_status IN ('Registered', 'Withdrawn', 'Completed')),
    created_at          TIMESTAMP    NOT NULL DEFAULT now(),
    UNIQUE (student_id, class_id)
);

CREATE TABLE IF NOT EXISTS enrollment_grade (
    enrollment_grade_id BIGSERIAL    PRIMARY KEY,
    enrollment_id       BIGINT       NOT NULL UNIQUE REFERENCES enrollment (enrollment_id),
    midterm_score       DECIMAL(5,2) CHECK (midterm_score BETWEEN 0 AND 10),
    final_score         DECIMAL(5,2) CHECK (final_score BETWEEN 0 AND 10),
    total_score         DECIMAL(5,2) CHECK (total_score BETWEEN 0 AND 10),
    grade_value         DECIMAL(4,2) CHECK (grade_value BETWEEN 0 AND 4),
    letter_grade        VARCHAR(5)
        CHECK (letter_grade IN ('A', 'B+', 'B', 'C+', 'C', 'D+', 'D', 'F')),
    registration_date   DATE,
    CHECK ((total_score IS NULL) = (letter_grade IS NULL)),
    CHECK ((grade_value IS NULL) = (letter_grade IS NULL))
);

CREATE INDEX IF NOT EXISTS ix_student_program_year     ON student (program_id, admission_year_id);
CREATE INDEX IF NOT EXISTS ix_curriculum_course_course ON curriculum_course (course_id);
CREATE INDEX IF NOT EXISTS ix_class_semester           ON class (semester_id);
CREATE INDEX IF NOT EXISTS ix_class_course             ON class (course_id);
CREATE INDEX IF NOT EXISTS ix_class_lecturer_lecturer  ON class_lecturer (lecturer_id);
CREATE INDEX IF NOT EXISTS ix_enrollment_class         ON enrollment (class_id);

-- Mỗi chương trình tối đa một khung Active (ACA-DQ-05); mỗi lớp đúng một giảng viên chính (ACA-DQ-07).
CREATE UNIQUE INDEX IF NOT EXISTS ux_curriculum_one_active
    ON curriculum (program_id) WHERE status = 'Active';
CREATE UNIQUE INDEX IF NOT EXISTS ux_class_lecturer_one_primary
    ON class_lecturer (class_id) WHERE is_primary;
