-- =============================================================================
-- UniLake AI - Script khởi tạo toàn bộ DDL cơ sở dữ liệu PostgreSQL
-- Container Docker PostgreSQL tự thực thi khi khởi động lần đầu
-- Sinh từ đặc tả DBML (database.md)
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app;
SET search_path TO app, public;

-- -----------------------------------------------------------------------------
-- 1. ĐÀO TẠO VÀ ĐƠN VỊ
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS department (
    department_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    department_code VARCHAR(30) UNIQUE NOT NULL,
    department_name VARCHAR(200) NOT NULL,
    department_type VARCHAR(50) NOT NULL,
    parent_department_id BIGINT REFERENCES department(department_id),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE department IS 'Khoa, Vien, Phong ban dung chung cho Academic va HR';

CREATE TABLE IF NOT EXISTS major (
    major_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    major_code VARCHAR(30) UNIQUE NOT NULL,
    major_name VARCHAR(200) NOT NULL,
    department_id BIGINT NOT NULL REFERENCES department(department_id),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS program (
    program_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    program_code VARCHAR(30) UNIQUE NOT NULL,
    program_name VARCHAR(200) NOT NULL,
    major_id BIGINT NOT NULL REFERENCES major(major_id),
    degree_level VARCHAR(50) NOT NULL,
    duration_years DECIMAL(4,1),
    tuition_fee_per_credit DECIMAL(15,2),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
);

-- -----------------------------------------------------------------------------
-- 2. TUYỂN SINH VÀ SINH VIÊN
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS admission_year (
    admission_year_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    year_label VARCHAR(20) UNIQUE NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE admission_year IS 'Phong Tuyen sinh - Quan ly dot tuyen sinh';

CREATE TABLE IF NOT EXISTS admission_method (
    admission_method_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    method_code VARCHAR(30) UNIQUE NOT NULL,
    method_name VARCHAR(150) NOT NULL,
    max_score DECIMAL(6,2) NOT NULL,
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS applicant (
    applicant_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    national_id VARCHAR(30) UNIQUE,
    full_name VARCHAR(200) NOT NULL,
    date_of_birth DATE,
    gender VARCHAR(20),
    phone VARCHAR(30),
    email VARCHAR(150),
    high_school_name VARCHAR(250),
    province VARCHAR(100),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE applicant IS 'Thong tin goc cua thi sinh dang ky tuyen sinh';

CREATE TABLE IF NOT EXISTS admission (
    admission_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    applicant_id BIGINT NOT NULL REFERENCES applicant(applicant_id),
    program_id BIGINT NOT NULL REFERENCES program(program_id),
    preference_rank INT NOT NULL,
    admission_method_id BIGINT NOT NULL REFERENCES admission_method(admission_method_id),
    admission_year_id BIGINT NOT NULL REFERENCES admission_year(admission_year_id),
    application_date DATE,
    admission_score DECIMAL(6,2),
    admission_status VARCHAR(30) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_admission UNIQUE (applicant_id, program_id, admission_year_id, admission_method_id)
);

CREATE TABLE IF NOT EXISTS student (
    student_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    student_code VARCHAR(30) UNIQUE NOT NULL,
    applicant_id BIGINT UNIQUE REFERENCES applicant(applicant_id),
    admission_id BIGINT UNIQUE REFERENCES admission(admission_id),
    full_name VARCHAR(200) NOT NULL,
    date_of_birth DATE,
    gender VARCHAR(20),
    program_id BIGINT NOT NULL REFERENCES program(program_id),
    admission_year_id BIGINT NOT NULL REFERENCES admission_year(admission_year_id),
    student_status VARCHAR(30) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE student IS 'Ho so sinh vien chinh thuc';

-- -----------------------------------------------------------------------------
-- 3. HỌC PHẦN VÀ LỚP HỌC
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS curriculum (
    curriculum_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    program_id BIGINT NOT NULL REFERENCES program(program_id),
    curriculum_code VARCHAR(50) NOT NULL,
    curriculum_name VARCHAR(250) NOT NULL,
    version VARCHAR(30) NOT NULL,
    effective_from DATE,
    effective_to DATE,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    CONSTRAINT uq_curriculum_version UNIQUE (program_id, version)
);

CREATE TABLE IF NOT EXISTS course (
    course_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    course_code VARCHAR(30) UNIQUE NOT NULL,
    course_name VARCHAR(200) NOT NULL,
    credit_hours INT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS curriculum_course (
    curriculum_course_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    curriculum_id BIGINT NOT NULL REFERENCES curriculum(curriculum_id),
    course_id BIGINT NOT NULL REFERENCES course(course_id),
    semester_no INT,
    course_type VARCHAR(30),
    is_required BOOLEAN NOT NULL DEFAULT TRUE,
    prerequisite_course_id BIGINT REFERENCES course(course_id),
    CONSTRAINT uq_curriculum_course UNIQUE (curriculum_id, course_id)
);

CREATE TABLE IF NOT EXISTS semester (
    semester_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    semester_code VARCHAR(30) UNIQUE NOT NULL,
    academic_year VARCHAR(20) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'UPCOMING'
);

CREATE TABLE IF NOT EXISTS class (
    class_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    class_code VARCHAR(50) UNIQUE NOT NULL,
    course_id BIGINT NOT NULL REFERENCES course(course_id),
    semester_id BIGINT NOT NULL REFERENCES semester(semester_id),
    max_capacity INT,
    room VARCHAR(50),
    class_status VARCHAR(30) NOT NULL
);

CREATE TABLE IF NOT EXISTS enrollment (
    enrollment_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    student_id BIGINT NOT NULL REFERENCES student(student_id),
    class_id BIGINT NOT NULL REFERENCES class(class_id),
    enrollment_date DATE NOT NULL,
    enrollment_status VARCHAR(30) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_enrollment UNIQUE (student_id, class_id)
);

CREATE TABLE IF NOT EXISTS enrollment_grade (
    enrollment_grade_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    enrollment_id BIGINT UNIQUE NOT NULL REFERENCES enrollment(enrollment_id),
    midterm_score DECIMAL(5,2),
    final_score DECIMAL(5,2),
    total_score DECIMAL(5,2),
    grade_value DECIMAL(4,2),
    letter_grade VARCHAR(5),
    registration_date DATE
);

-- -----------------------------------------------------------------------------
-- 4. NHÂN SỰ VÀ GIẢNG VIÊN
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS employee (
    employee_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_code VARCHAR(30) UNIQUE NOT NULL,
    full_name VARCHAR(200) NOT NULL,
    date_of_birth DATE,
    gender VARCHAR(20),
    email VARCHAR(150) UNIQUE,
    phone VARCHAR(30),
    hire_date DATE,
    employee_status VARCHAR(30) NOT NULL
);

CREATE TABLE IF NOT EXISTS position (
    position_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    position_code VARCHAR(30) UNIQUE NOT NULL,
    position_name VARCHAR(150) NOT NULL,
    position_level VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS assignment (
    assignment_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_id BIGINT NOT NULL REFERENCES employee(employee_id),
    department_id BIGINT NOT NULL REFERENCES department(department_id),
    position_id BIGINT NOT NULL REFERENCES position(position_id),
    start_date DATE NOT NULL,
    end_date DATE,
    is_primary BOOLEAN NOT NULL DEFAULT TRUE,
    assignment_status VARCHAR(30) NOT NULL
);

CREATE TABLE IF NOT EXISTS lecturer (
    employee_id BIGINT PRIMARY KEY REFERENCES employee(employee_id),
    academic_degree VARCHAR(100),
    academic_title VARCHAR(250),
    research_field VARCHAR(250)
);

CREATE TABLE IF NOT EXISTS class_lecturer (
    class_lecturer_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    class_id BIGINT NOT NULL REFERENCES class(class_id),
    lecturer_id BIGINT NOT NULL REFERENCES lecturer(employee_id),
    teaching_role VARCHAR(50) DEFAULT 'PRIMARY_LECTURER',
    is_primary BOOLEAN NOT NULL DEFAULT TRUE,
    assigned_from DATE,
    assigned_to DATE,
    CONSTRAINT uq_class_lecturer UNIQUE (class_id, lecturer_id)
);

CREATE TABLE IF NOT EXISTS qualification (
    qualification_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    qualification_name VARCHAR(150) NOT NULL,
    qualification_type VARCHAR(100)
);

CREATE TABLE IF NOT EXISTS employee_qualification (
    employee_id BIGINT REFERENCES employee(employee_id),
    qualification_id BIGINT REFERENCES qualification(qualification_id),
    institution VARCHAR(250),
    year_obtained INT,
    PRIMARY KEY (employee_id, qualification_id)
);

-- -----------------------------------------------------------------------------
-- 5. BẢO MẬT VÀ PHÂN QUYỀN RBAC
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS app_user (
    app_user_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    employee_id BIGINT UNIQUE REFERENCES employee(employee_id),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS role (
    role_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    role_code VARCHAR(50) UNIQUE NOT NULL,
    role_name VARCHAR(100) NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS permission (
    permission_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    permission_code VARCHAR(100) UNIQUE NOT NULL,
    permission_name VARCHAR(150) NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS user_role (
    app_user_id BIGINT REFERENCES app_user(app_user_id),
    role_id BIGINT REFERENCES role(role_id),
    PRIMARY KEY (app_user_id, role_id)
);

CREATE TABLE IF NOT EXISTS role_permission (
    role_id BIGINT REFERENCES role(role_id),
    permission_id BIGINT REFERENCES permission(permission_id),
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE IF NOT EXISTS login_session (
    login_session_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    app_user_id BIGINT NOT NULL REFERENCES app_user(app_user_id),
    login_at TIMESTAMP NOT NULL DEFAULT NOW(),
    logout_at TIMESTAMP,
    ip_address VARCHAR(45),
    user_agent TEXT,
    refresh_token_hash TEXT,
    session_status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE'
);


-- -----------------------------------------------------------------------------
-- 6. NỀN TẢNG DỮ LIỆU VÀ BỘ XỬ LÝ DATA LAKE
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS source_system (
    source_system_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_code VARCHAR(50) UNIQUE NOT NULL,
    source_name VARCHAR(200) NOT NULL,
    source_type VARCHAR(50) NOT NULL,
    description TEXT,
    owner_department_id BIGINT REFERENCES department(department_id),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS data_source (
    data_source_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_system_id BIGINT NOT NULL REFERENCES source_system(source_system_id),
    source_name VARCHAR(200) NOT NULL,
    source_type VARCHAR(50) NOT NULL,
    connection_type VARCHAR(50),
    location TEXT,
    configuration JSONB NOT NULL DEFAULT '{}',
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_ingested_at TIMESTAMP,
    last_ingestion_status VARCHAR(30),
    ingestion_count INT NOT NULL DEFAULT 0,
    created_by BIGINT REFERENCES app_user(app_user_id),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS data_layer (
    data_layer_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    layer_code VARCHAR(20) UNIQUE NOT NULL,
    layer_name VARCHAR(50) NOT NULL,
    storage_type VARCHAR(50),
    description TEXT
);

CREATE TABLE IF NOT EXISTS dataset (
    dataset_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_code VARCHAR(100) UNIQUE NOT NULL,
    dataset_name VARCHAR(200) NOT NULL,
    domain VARCHAR(50) NOT NULL,
    source_system_id BIGINT REFERENCES source_system(source_system_id),
    data_layer_id BIGINT NOT NULL REFERENCES data_layer(data_layer_id),
    owner_department_id BIGINT REFERENCES department(department_id),
    storage_format VARCHAR(50),
    location TEXT,
    version VARCHAR(30),
    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE',
    description TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ingestion_job (
    ingestion_job_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    data_source_id BIGINT NOT NULL REFERENCES data_source(data_source_id),
    dataset_id BIGINT NOT NULL REFERENCES dataset(dataset_id),
    target_layer_id BIGINT NOT NULL REFERENCES data_layer(data_layer_id),
    job_type VARCHAR(50),
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    status VARCHAR(30) NOT NULL,
    rows_read BIGINT,
    rows_written BIGINT,
    rows_rejected BIGINT,
    error_message TEXT,
    source_table VARCHAR(200),
    created_at TIMESTAMP,
    requested_by VARCHAR(100),
    stage VARCHAR(30),
    lease_expires_at TIMESTAMP,
    output JSONB,
    validation_input JSONB,
    validation_report JSONB
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ingestion_execution_active_source
    ON ingestion_job(data_source_id)
    WHERE job_type = 'EXECUTION_V1' AND status IN ('QUEUED', 'RUNNING');
CREATE INDEX IF NOT EXISTS ix_ingestion_execution_queue
    ON ingestion_job(status, ingestion_job_id) WHERE job_type = 'EXECUTION_V1';

CREATE TABLE IF NOT EXISTS transformation_step (
    transformation_step_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingestion_job_id BIGINT REFERENCES ingestion_job(ingestion_job_id),
    source_dataset_id BIGINT NOT NULL REFERENCES dataset(dataset_id),
    target_dataset_id BIGINT NOT NULL REFERENCES dataset(dataset_id),
    step_name VARCHAR(150) NOT NULL,
    step_order INT NOT NULL,
    transformation_type VARCHAR(50) NOT NULL,
    transformation_expression TEXT,
    rows_input BIGINT,
    rows_output BIGINT,
    status VARCHAR(30) NOT NULL,
    executed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS data_processing_run (
    run_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_type VARCHAR(50) NOT NULL,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    status VARCHAR(30) NOT NULL,
    triggered_by BIGINT REFERENCES app_user(app_user_id),
    error_message TEXT
);

-- -----------------------------------------------------------------------------
-- 7. QUẢN TRỊ DỮ LIỆU (CATALOG, NGUỒN GỐC, CHẤT LƯỢNG)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog_table (
    catalog_table_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id BIGINT NOT NULL REFERENCES dataset(dataset_id),
    table_name VARCHAR(150) NOT NULL,
    source_table_name VARCHAR(150),
    description TEXT,
    owner VARCHAR(150),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP,
    CONSTRAINT uq_catalog_table UNIQUE (dataset_id, table_name)
);

CREATE TABLE IF NOT EXISTS catalog_column (
    catalog_column_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    catalog_table_id BIGINT NOT NULL REFERENCES catalog_table(catalog_table_id),
    column_name VARCHAR(150) NOT NULL,
    data_type VARCHAR(50) NOT NULL,
    description TEXT,
    is_nullable BOOLEAN,
    is_primary_key BOOLEAN NOT NULL DEFAULT FALSE,
    is_foreign_key BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_catalog_column UNIQUE (catalog_table_id, column_name)
);

CREATE TABLE IF NOT EXISTS data_lineage (
    data_lineage_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    transformation_step_id BIGINT NOT NULL REFERENCES transformation_step(transformation_step_id),
    source_column_id BIGINT NOT NULL REFERENCES catalog_column(catalog_column_id),
    target_column_id BIGINT NOT NULL REFERENCES catalog_column(catalog_column_id),
    lineage_type VARCHAR(50) NOT NULL,
    transformation_expression TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS data_quality_rule (
    data_quality_rule_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    catalog_column_id BIGINT NOT NULL REFERENCES catalog_column(catalog_column_id),
    rule_code VARCHAR(50) UNIQUE NOT NULL,
    rule_name VARCHAR(150) NOT NULL,
    rule_type VARCHAR(50) NOT NULL,
    rule_expression TEXT NOT NULL,
    severity VARCHAR(20) NOT NULL DEFAULT 'HIGH',
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS data_quality_run (
    data_quality_run_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    data_quality_rule_id BIGINT NOT NULL REFERENCES data_quality_rule(data_quality_rule_id),
    ingestion_job_id BIGINT REFERENCES ingestion_job(ingestion_job_id),
    run_at TIMESTAMP NOT NULL DEFAULT NOW(),
    total_records BIGINT NOT NULL,
    passed_records BIGINT NOT NULL,
    failed_records BIGINT NOT NULL,
    pass_rate DECIMAL(5,2) NOT NULL,
    execution_status VARCHAR(30) NOT NULL,
    failure_sample_ref TEXT
);

-- -----------------------------------------------------------------------------
-- 8. PHÂN TÍCH AI VÀ CHUYỂN VĂN BẢN THÀNH SQL
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ai_model (
    ai_model_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider VARCHAR(100) NOT NULL,
    model_name VARCHAR(150) NOT NULL,
    model_version VARCHAR(100),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS prompt_template (
    prompt_template_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    template_name VARCHAR(150) NOT NULL,
    version VARCHAR(30) NOT NULL,
    template_text TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_prompt_template UNIQUE (template_name, version)
);

CREATE TABLE IF NOT EXISTS query_request (
    query_request_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    app_user_id BIGINT NOT NULL REFERENCES app_user(app_user_id),
    question_text TEXT NOT NULL,
    language VARCHAR(20) DEFAULT 'vi',
    detected_intent VARCHAR(150),
    submitted_at TIMESTAMP NOT NULL DEFAULT NOW(),
    status VARCHAR(30) NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_retrieval (
    schema_retrieval_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    query_request_id BIGINT NOT NULL REFERENCES query_request(query_request_id),
    catalog_table_id BIGINT NOT NULL REFERENCES catalog_table(catalog_table_id),
    retrieval_rank INT NOT NULL,
    relevance_score DECIMAL(6,4),
    CONSTRAINT uq_schema_retrieval UNIQUE (query_request_id, catalog_table_id)
);

CREATE TABLE IF NOT EXISTS generated_sql (
    generated_sql_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    query_request_id BIGINT NOT NULL REFERENCES query_request(query_request_id),
    ai_model_id BIGINT NOT NULL REFERENCES ai_model(ai_model_id),
    prompt_template_id BIGINT NOT NULL REFERENCES prompt_template(prompt_template_id),
    sql_text TEXT NOT NULL,
    generated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    validation_status VARCHAR(30) NOT NULL,
    validation_error TEXT
);

CREATE TABLE IF NOT EXISTS query_execution (
    query_execution_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    generated_sql_id BIGINT UNIQUE NOT NULL REFERENCES generated_sql(generated_sql_id),
    executed_at TIMESTAMP NOT NULL DEFAULT NOW(),
    execution_status VARCHAR(30) NOT NULL,
    row_count BIGINT,
    execution_time_ms INT,
    result_ref TEXT,
    error_message TEXT
);

-- -----------------------------------------------------------------------------
-- 9. BẢNG ĐIỀU KHIỂN VÀ TRỰC QUAN HÓA
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS dashboard (
    dashboard_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dashboard_name VARCHAR(150) NOT NULL,
    owner_app_user_id BIGINT NOT NULL REFERENCES app_user(app_user_id),
    description TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kpi (
    kpi_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kpi_code VARCHAR(50) UNIQUE NOT NULL,
    kpi_name VARCHAR(150) NOT NULL,
    domain VARCHAR(50) NOT NULL,
    unit VARCHAR(50),
    description TEXT
);

CREATE TABLE IF NOT EXISTS kpi_value (
    kpi_value_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kpi_id BIGINT NOT NULL REFERENCES kpi(kpi_id),
    period_date DATE NOT NULL,
    kpi_value DECIMAL(20,4) NOT NULL,
    dimension_json TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_kpi_value_period ON kpi_value(kpi_id, period_date);

CREATE TABLE IF NOT EXISTS chart_widget (
    chart_widget_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dashboard_id BIGINT NOT NULL REFERENCES dashboard(dashboard_id),
    widget_type VARCHAR(50) NOT NULL,
    title VARCHAR(200) NOT NULL,
    query_request_id BIGINT REFERENCES query_request(query_request_id),
    kpi_id BIGINT REFERENCES kpi(kpi_id),
    config_json TEXT,
    position_x INT,
    position_y INT,
    width INT,
    height INT
);
