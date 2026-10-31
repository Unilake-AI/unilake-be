-- UniLake AI V1.0 - Schema dataset Tuyển sinh V1
-- DDL PostgreSQL cho các bảng Admissions, bám đúng docs/data-schema/unilake-db-architecture.txt:
-- tên cột, thứ tự, NULL/NOT NULL, PK, UNIQUE, FK lấy từ DB; độ dài varchar/decimal và CHECK
-- là đề xuất của dataset (DB chỉ khai báo kiểu chung string/decimal). Xem admissions_v1.schema.json.
-- Chạy sau hr_v1.sql (department). Bảng student nằm ở academic_v1.sql.

CREATE TABLE IF NOT EXISTS major (
    major_id            BIGSERIAL    PRIMARY KEY,
    major_code          VARCHAR(30)  NOT NULL UNIQUE CHECK (major_code ~ '^[0-9]{7}$'),
    major_name          VARCHAR(200) NOT NULL,
    department_id       BIGINT       NOT NULL REFERENCES department (department_id),
    status              VARCHAR(20)  NOT NULL DEFAULT 'Active' CHECK (status IN ('Active', 'Inactive'))
);

CREATE TABLE IF NOT EXISTS program (
    program_id              BIGSERIAL     PRIMARY KEY,
    program_code            VARCHAR(30)   NOT NULL UNIQUE,
    program_name            VARCHAR(200)  NOT NULL,
    major_id                BIGINT        NOT NULL REFERENCES major (major_id),
    degree_level            VARCHAR(50)   NOT NULL CHECK (degree_level IN ('Bachelor', 'Engineer', 'Master')),
    duration_years          DECIMAL(4,1)  CHECK (duration_years BETWEEN 1 AND 6),
    tuition_fee_per_credit  DECIMAL(15,2) CHECK (tuition_fee_per_credit >= 0),
    status                  VARCHAR(20)   NOT NULL DEFAULT 'Active'
        CHECK (status IN ('Active', 'Inactive', 'Deprecated'))
);

CREATE TABLE IF NOT EXISTS admission_method (
    admission_method_id BIGSERIAL    PRIMARY KEY,
    method_code         VARCHAR(30)  NOT NULL UNIQUE,
    method_name         VARCHAR(150) NOT NULL,
    max_score           DECIMAL(6,2) NOT NULL CHECK (max_score >= 0),
    description         TEXT,
    is_active           BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS admission_year (
    admission_year_id   BIGSERIAL    PRIMARY KEY,
    year_label          VARCHAR(20)  NOT NULL UNIQUE CHECK (year_label ~ '^[0-9]{4}-[0-9]{4}$'),
    start_date          DATE         NOT NULL,
    end_date            DATE         NOT NULL,
    status              VARCHAR(20)  NOT NULL CHECK (status IN ('Upcoming', 'Open', 'Closed')),
    created_at          TIMESTAMP    NOT NULL DEFAULT now(),
    CHECK (end_date > start_date)
);

CREATE TABLE IF NOT EXISTS applicant (
    applicant_id        BIGSERIAL    PRIMARY KEY,
    national_id         VARCHAR(30)  UNIQUE,
    full_name           VARCHAR(200) NOT NULL,
    date_of_birth       DATE,
    gender              VARCHAR(20)  CHECK (gender IN ('Male', 'Female')),
    phone               VARCHAR(30),
    email               VARCHAR(150),
    high_school_name    VARCHAR(250),
    province            VARCHAR(100),
    created_at          TIMESTAMP    NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS admission (
    admission_id        BIGSERIAL    PRIMARY KEY,
    applicant_id        BIGINT       NOT NULL REFERENCES applicant (applicant_id),
    program_id          BIGINT       NOT NULL REFERENCES program (program_id),
    preference_rank     INT          NOT NULL CHECK (preference_rank BETWEEN 1 AND 99),
    admission_method_id BIGINT       NOT NULL REFERENCES admission_method (admission_method_id),
    admission_year_id   BIGINT       NOT NULL REFERENCES admission_year (admission_year_id),
    application_date    DATE,
    admission_score     DECIMAL(6,2) CHECK (admission_score >= 0),
    admission_status    VARCHAR(30)  NOT NULL
        CHECK (admission_status IN ('Applied', 'Accepted', 'Rejected', 'Waitlisted')),
    created_at          TIMESTAMP    NOT NULL DEFAULT now(),
    UNIQUE (applicant_id, admission_year_id, preference_rank),
    UNIQUE (applicant_id, program_id, admission_year_id, admission_method_id)
);

CREATE INDEX IF NOT EXISTS ix_major_department       ON major (department_id);
CREATE INDEX IF NOT EXISTS ix_program_major          ON program (major_id);
CREATE INDEX IF NOT EXISTS ix_admission_year_program ON admission (admission_year_id, program_id);
CREATE INDEX IF NOT EXISTS ix_admission_method       ON admission (admission_method_id);

-- Mỗi thí sinh tối đa một hồ sơ Accepted mỗi mùa (ADM-DQ-07).
CREATE UNIQUE INDEX IF NOT EXISTS ux_admission_one_accepted
    ON admission (applicant_id, admission_year_id) WHERE admission_status = 'Accepted';
