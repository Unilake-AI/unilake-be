-- UniLake AI V1.0 - Schema dataset Nhân sự V1
-- DDL PostgreSQL cho các bảng HR, bám đúng docs/data-schema/unilake-db-architecture.txt:
-- tên cột, thứ tự, NULL/NOT NULL, PK, UNIQUE, FK lấy từ DB; độ dài varchar/decimal và CHECK
-- là đề xuất của dataset (DB chỉ khai báo kiểu chung string/decimal). Xem hr_v1.schema.json.
-- Thứ tự chạy DDL: hr_v1.sql -> admissions_v1.sql -> academic_v1.sql.

CREATE TABLE IF NOT EXISTS department (
    department_id           BIGSERIAL    PRIMARY KEY,
    department_code         VARCHAR(30)  NOT NULL UNIQUE,
    department_name         VARCHAR(200) NOT NULL,
    department_type         VARCHAR(30)  NOT NULL
        CHECK (department_type IN ('University', 'Board', 'Office', 'Faculty', 'Division')),
    parent_department_id    BIGINT       REFERENCES department (department_id),
    status                  VARCHAR(20)  NOT NULL DEFAULT 'Active' CHECK (status IN ('Active', 'Inactive')),
    created_at              TIMESTAMP    NOT NULL DEFAULT now(),
    CHECK (parent_department_id IS DISTINCT FROM department_id)
);

CREATE TABLE IF NOT EXISTS position (
    position_id         BIGSERIAL    PRIMARY KEY,
    position_code       VARCHAR(30)  NOT NULL UNIQUE,
    position_name       VARCHAR(150) NOT NULL,
    position_level      VARCHAR(50)  CHECK (position_level IN ('Leadership', 'Management', 'Staff'))
);

CREATE TABLE IF NOT EXISTS qualification (
    qualification_id    BIGSERIAL    PRIMARY KEY,
    qualification_name  VARCHAR(150) NOT NULL,
    qualification_type  VARCHAR(100) CHECK (qualification_type IN ('Degree', 'Certificate'))
);

-- DB không còn UNIQUE trên qualification_name; đề xuất khôi phục (HR-DQ-13).
CREATE UNIQUE INDEX IF NOT EXISTS ux_qualification_name ON qualification (qualification_name);

CREATE TABLE IF NOT EXISTS employee (
    employee_id         BIGSERIAL    PRIMARY KEY,
    employee_code       VARCHAR(30)  NOT NULL UNIQUE,
    full_name           VARCHAR(200) NOT NULL,
    date_of_birth       DATE,
    gender              VARCHAR(20)  CHECK (gender IN ('Male', 'Female')),
    email               VARCHAR(150) UNIQUE,
    phone               VARCHAR(30),
    hire_date           DATE,
    employee_status     VARCHAR(30)  NOT NULL
        CHECK (employee_status IN ('Active', 'Resigned', 'Retired')),
    CHECK (hire_date IS NULL OR date_of_birth IS NULL OR hire_date >= date_of_birth + INTERVAL '18 years')
);

CREATE TABLE IF NOT EXISTS lecturer (
    employee_id         BIGINT       PRIMARY KEY REFERENCES employee (employee_id),
    academic_degree     VARCHAR(50)  CHECK (academic_degree IN ('B.Sc.', 'M.Sc.', 'Ph.D.')),
    academic_title      VARCHAR(50)  CHECK (academic_title IN ('Assoc. Prof.', 'Prof.')),
    research_field      VARCHAR(250),
    CHECK (academic_title IS NULL OR academic_degree = 'Ph.D.')
);

CREATE TABLE IF NOT EXISTS employee_qualification (
    employee_id         BIGINT       NOT NULL REFERENCES employee (employee_id),
    qualification_id    BIGINT       NOT NULL REFERENCES qualification (qualification_id),
    institution         VARCHAR(250),
    year_obtained       INT          CHECK (year_obtained BETWEEN 1960 AND 2100),
    PRIMARY KEY (employee_id, qualification_id)
);

CREATE TABLE IF NOT EXISTS assignment (
    assignment_id       BIGSERIAL    PRIMARY KEY,
    employee_id         BIGINT       NOT NULL REFERENCES employee (employee_id),
    department_id       BIGINT       NOT NULL REFERENCES department (department_id),
    position_id         BIGINT       NOT NULL REFERENCES position (position_id),
    start_date          DATE         NOT NULL,
    end_date            DATE,
    is_primary          BOOLEAN      NOT NULL DEFAULT TRUE,
    assignment_status   VARCHAR(30)  NOT NULL CHECK (assignment_status IN ('Active', 'Ended')),
    CHECK (end_date IS NULL OR end_date >= start_date)
);

CREATE INDEX IF NOT EXISTS ix_department_parent   ON department (parent_department_id);
CREATE INDEX IF NOT EXISTS ix_assignment_employee ON assignment (employee_id, start_date);
CREATE INDEX IF NOT EXISTS ix_assignment_dept_pos ON assignment (department_id, position_id);

-- Mỗi nhân sự chỉ có một vị trí chính đang mở (HR-DQ-07).
CREATE UNIQUE INDEX IF NOT EXISTS ux_assignment_one_open_primary
    ON assignment (employee_id) WHERE is_primary AND end_date IS NULL;
