// ==========================================================
// UniLake AI - Standardized Database Architecture (DBML)
// Refactored per Faculty Review & In-Scope Architecture
// Target DBMS: PostgreSQL / Data Lake Ingestion Layer
// ==========================================================

Project UniLakeAI {
  database_type: 'PostgreSQL'
  Note: 'Enterprise Data Lake & AI Analytics Platform Database for University Administration'
}

//// ========================================================
//// 1. BUSINESS DATA - ADMISSIONS
//// ========================================================

Table admission_year {
  admission_year_id bigint [pk, increment]
  year_label varchar(20) [unique, not null, note: 'e.g. 2025-2026']
  start_date date [not null]
  end_date date [not null]
  status varchar(20) [not null, note: 'OPEN, CLOSED, UPCOMING']
  created_at timestamp [not null, default: `now()` ]

  Note: 'Phong Tuyen sinh - Quan ly dot tuyen sinh'
}

Table admission_method {
  admission_method_id bigint [pk, increment]
  method_code varchar(30) [unique, not null, note: 'THPT, HOC_BA, DGNL, TUYEN_THANG']
  method_name varchar(150) [not null]
  max_score decimal(6,2) [not null]
  description text
  is_active boolean [not null, default: true]
}

Table applicant {
  applicant_id bigint [pk, increment]
  national_id varchar(30) [unique]
  full_name varchar(200) [not null]
  date_of_birth date
  gender varchar(20)
  phone varchar(30)
  email varchar(150)
  high_school_name varchar(250)
  province varchar(100)
  created_at timestamp [not null, default: `now()` ]

  Note: 'Thong tin goc cua thi sinh dang ky tuyen sinh'
}

Table admission {
  admission_id bigint [pk, increment]
  applicant_id bigint [not null, ref: > applicant.applicant_id]
  program_id bigint [not null, ref: > program.program_id]
  preference_rank int [not null]
  admission_method_id bigint [not null, ref: > admission_method.admission_method_id]
  admission_year_id bigint [not null, ref: > admission_year.admission_year_id]
  application_date date
  admission_score decimal(6,2)
  admission_status varchar(30) [not null, note: 'APPLIED, ADMITTED, REJECTED, WITHDRAWN']
  created_at timestamp [not null, default: `now()` ]

  indexes {
    (applicant_id, program_id, admission_year_id, admission_method_id) [unique]
  }
}

Table student {
  student_id bigint [pk, increment]
  student_code varchar(30) [unique, not null]
  applicant_id bigint [unique, ref: > applicant.applicant_id, note: 'FK toi applicant de tranh duplicate thong tin ca nhan']
  admission_id bigint [unique, ref: > admission.admission_id]
  full_name varchar(200) [not null]
  date_of_birth date
  gender varchar(20)
  program_id bigint [not null, ref: > program.program_id]
  admission_year_id bigint [not null, ref: > admission_year.admission_year_id]
  student_status varchar(30) [not null, note: 'ACTIVE, GRADUATED, SUSPENDED, DROPPED']
  created_at timestamp [not null, default: `now()` ]

  Note: 'Ho so sinh vien chinh thuc'
}

Table enrollment {
  enrollment_id bigint [pk, increment]
  student_id bigint [not null, ref: > student.student_id]
  class_id bigint [not null, ref: > class.class_id]
  enrollment_date date [not null]
  enrollment_status varchar(30) [not null, note: 'ENROLLED, DROPPED, COMPLETED']
  created_at timestamp [not null, default: `now()` ]

  indexes {
    (student_id, class_id) [unique]
  }
  Note: 'Dang ky hoc phan cua sinh vien'
}

//// ========================================================
//// 2. BUSINESS DATA - ACADEMIC / TRAINING
//// ========================================================

Table department {
  department_id bigint [pk, increment]
  department_code varchar(30) [unique, not null]
  department_name varchar(200) [not null]
  department_type varchar(50) [not null]
  parent_department_id bigint [ref: > department.department_id]
  status varchar(20) [not null, default: 'ACTIVE']
  created_at timestamp [not null, default: `now()` ]

  Note: 'Khoa, Vien, Phong ban dung chung cho Academic va HR'
}

Table major {
  major_id bigint [pk, increment]
  major_code varchar(30) [unique, not null]
  major_name varchar(200) [not null]
  department_id bigint [not null, ref: > department.department_id]
  status varchar(20) [not null, default: 'ACTIVE']
}

Table program {
  program_id bigint [pk, increment]
  program_code varchar(30) [unique, not null]
  program_name varchar(200) [not null]
  major_id bigint [not null, ref: > major.major_id]
  degree_level varchar(50) [not null, note: 'BACHELOR, MASTER, DOCTOR']
  duration_years decimal(4,1)
  tuition_fee_per_credit decimal(15,2)
  status varchar(20) [not null, default: 'ACTIVE']
}

Table curriculum {
  curriculum_id bigint [pk, increment]
  program_id bigint [not null, ref: > program.program_id]
  curriculum_code varchar(50) [not null]
  curriculum_name varchar(250) [not null]
  version varchar(30) [not null, note: 'e.g. K27-2023']
  effective_from date
  effective_to date
  status varchar(20) [not null, default: 'ACTIVE']

  indexes {
    (program_id, version) [unique]
  }
  Note: 'Khung chuong trinh dao tao theo khoa/nam'
}

Table course {
  course_id bigint [pk, increment]
  course_code varchar(30) [unique, not null]
  course_name varchar(200) [not null]
  credit_hours int [not null]
  status varchar(20) [not null, default: 'ACTIVE']

  Note: 'Danh muc mon hoc tong the, doc lap voi major'
}

Table curriculum_course {
  curriculum_course_id bigint [pk, increment]
  curriculum_id bigint [not null, ref: > curriculum.curriculum_id]
  course_id bigint [not null, ref: > course.course_id]
  semester_no int [note: 'Hoc ky khuyen nghi (1 -> 8)']
  course_type varchar(30) [note: 'COMPULSORY, ELECTIVE, GENERAL']
  is_required boolean [not null, default: true]
  prerequisite_course_id bigint [ref: > course.course_id]

  indexes {
    (curriculum_id, course_id) [unique]
  }
}

Table semester {
  semester_id bigint [pk, increment]
  semester_code varchar(30) [unique, not null, note: 'e.g. 2026-1']
  academic_year varchar(20) [not null, note: 'e.g. 2025-2026']
  start_date date [not null]
  end_date date [not null]
  status varchar(20) [not null, default: 'UPCOMING']
}

Table class {
  class_id bigint [pk, increment]
  class_code varchar(50) [unique, not null]
  course_id bigint [not null, ref: > course.course_id]
  semester_id bigint [not null, ref: > semester.semester_id]
  max_capacity int
  room varchar(50)
  class_status varchar(30) [not null, note: 'PLANNED, OPEN, IN_PROGRESS, CLOSED']
}

Table class_lecturer {
  class_lecturer_id bigint [pk, increment]
  class_id bigint [not null, ref: > class.class_id]
  lecturer_id bigint [not null, ref: > lecturer.employee_id]
  teaching_role varchar(50) [default: 'PRIMARY_LECTURER']
  is_primary boolean [not null, default: true]
  assigned_from date
  assigned_to date

  indexes {
    (class_id, lecturer_id) [unique]
  }
  Note: 'Ho tro 1 lop nhieu giang vien giang day / tro giang'
}

Table enrollment_grade {
  enrollment_grade_id bigint [pk, increment]
  enrollment_id bigint [unique, not null, ref: - enrollment.enrollment_id]
  midterm_score decimal(5,2)
  final_score decimal(5,2)
  total_score decimal(5,2)
  grade_value decimal(4,2)
  letter_grade varchar(5)
  registration_date date
}

//// ========================================================
//// 3. BUSINESS DATA - HUMAN RESOURCES
//// ========================================================

Table employee {
  employee_id bigint [pk, increment]
  employee_code varchar(30) [unique, not null]
  full_name varchar(200) [not null]
  date_of_birth date
  gender varchar(20)
  email varchar(150) [unique]
  phone varchar(30)
  hire_date date
  employee_status varchar(30) [not null, note: 'ACTIVE, RESIGNED, RETIRED, ON_LEAVE']
}

Table position {
  position_id bigint [pk, increment]
  position_code varchar(30) [unique, not null]
  position_name varchar(150) [not null]
  position_level varchar(50)
}

Table assignment {
  assignment_id bigint [pk, increment]
  employee_id bigint [not null, ref: > employee.employee_id]
  department_id bigint [not null, ref: > department.department_id]
  position_id bigint [not null, ref: > position.position_id]
  start_date date [not null]
  end_date date
  is_primary boolean [not null, default: true]
  assignment_status varchar(30) [not null, note: 'ACTIVE, ENDED, SUSPENDED']
}

Table lecturer {
  employee_id bigint [pk, ref: - employee.employee_id]
  academic_degree varchar(100) [note: 'MSc, PhD, Assoc. Prof., Prof.']
  academic_title varchar(250) [null]
  research_field varchar(250)
}

Table qualification {
  qualification_id bigint [pk, increment]
  qualification_name varchar(150) [not null]
  qualification_type varchar(100) [note: 'DEGREE, CERTIFICATE']
}

Table employee_qualification {
  employee_id bigint [ref: > employee.employee_id]
  qualification_id bigint [ref: > qualification.qualification_id]
  institution varchar(250)
  year_obtained int

  indexes {
    (employee_id, qualification_id) [pk]
  }
}

//// ========================================================
//// 4. DATA PLATFORM & LAKE ENGINE
//// ========================================================

Table source_system {
  source_system_id bigint [pk, increment]
  source_code varchar(50) [unique, not null]
  source_name varchar(200) [not null]
  source_type varchar(50) [not null, note: 'RDBMS, REST_API, FILE_STORAGE, SPREADSHEET']
  description text
  owner_department_id bigint [ref: > department.department_id]
  is_active boolean [not null, default: true]
  created_at timestamp [not null, default: `now()` ]

  Note: 'Hệ thống nguồn nghiệp vụ (Admissions System, Academic System, HR System...)'
}

Table data_source {
  data_source_id bigint [pk, increment]
  source_system_id bigint [not null, ref: > source_system.source_system_id]

  // --- Thông tin định danh ---
  source_name varchar(200) [not null, note: 'Tên nguồn dữ liệu cụ thể, ví dụ: Admissions CSV 2026']
  source_type varchar(50) [not null, note: 'CSV, EXCEL, JSON, PostgreSQL, MySQL, MinIO, REST_API']
  connection_type varchar(50) [note: 'FILE, JDBC, REST, S3_COMPATIBLE']

  // --- Vị trí / Endpoint ---
  location text [note: 'Đường dẫn file, bucket path, hoặc host endpoint']

  // --- Cấu hình kỹ thuật linh hoạt theo source_type ---
  configuration jsonb [not null, default: '{}', note: 'Cấu hình chi tiết theo từng loại nguồn. Ví dụ CSV: {delimiter, encoding, has_header}; Excel: {sheet_name, header_row}; API: {method, headers, pagination}']

  // --- Metadata vận hành ---
  description text
  is_active boolean [not null, default: true]
  last_ingested_at timestamp [note: 'Thời điểm ingestion thành công gần nhất']
  last_ingestion_status varchar(30) [note: 'SUCCESS, FAILED, RUNNING']
  ingestion_count int [not null, default: 0]

  created_by bigint [ref: > app_user.app_user_id]
  created_at timestamp [not null, default: `now()` ]
  updated_at timestamp

  Note: 'Cấu hình nguồn dữ liệu cụ thể dùng cho Ingestion. Đây là bảng chính của user story Quản lý nguồn dữ liệu'
}

Table data_layer {
  data_layer_id bigint [pk, increment]
  layer_code varchar(20) [unique, not null, note: 'BRONZE, SILVER, GOLD']
  layer_name varchar(50) [not null]
  storage_type varchar(50) [note: 'MinIO_S3, DeltaLake, PostgreSQL']
  description text
}

Table dataset {
  dataset_id bigint [pk, increment]
  dataset_code varchar(100) [unique, not null]
  dataset_name varchar(200) [not null]
  domain varchar(50) [not null, note: 'Admissions, Academic, HR']
  source_system_id bigint [ref: > source_system.source_system_id]
  data_layer_id bigint [not null, ref: > data_layer.data_layer_id]
  owner_department_id bigint [ref: > department.department_id]
  storage_format varchar(50) [note: 'PARQUET, DELTA, CSV, RELATIONAL_TABLE']
  location text
  version varchar(30)
  status varchar(30) [not null, default: 'ACTIVE']
  description text
  created_at timestamp [not null, default: `now()` ]
  updated_at timestamp
}

Table ingestion_job {
  ingestion_job_id bigint [pk, increment]
  data_source_id bigint [not null, ref: > data_source.data_source_id]
  dataset_id bigint [not null, ref: > dataset.dataset_id]
  target_layer_id bigint [not null, ref: > data_layer.data_layer_id]
  job_type varchar(50) [note: 'FULL_LOAD, INCREMENTAL']
  started_at timestamp
  finished_at timestamp
  status varchar(30) [not null, note: 'RUNNING, SUCCESS, FAILED']
  rows_read bigint
  rows_written bigint
  rows_rejected bigint
  error_message text
}

Table transformation_step {
  transformation_step_id bigint [pk, increment]
  ingestion_job_id bigint [ref: > ingestion_job.ingestion_job_id]
  source_dataset_id bigint [not null, ref: > dataset.dataset_id]
  target_dataset_id bigint [not null, ref: > dataset.dataset_id]
  step_name varchar(150) [not null]
  step_order int [not null]
  transformation_type varchar(50) [not null, note: 'CLEANING, STANDARDIZATION, DEDUPLICATION, AGGREGATION']
  transformation_expression text
  rows_input bigint
  rows_output bigint
  status varchar(30) [not null, note: 'RUNNING, SUCCESS, FAILED']
  executed_at timestamp
}

Table data_processing_run {
  run_id bigint [pk, increment]
  run_type varchar(50) [not null, note: 'ETL_DAILY, REINDEX_CATALOG, AD_HOC']
  started_at timestamp
  finished_at timestamp
  status varchar(30) [not null, note: 'RUNNING, SUCCESS, FAILED']
  triggered_by bigint [ref: > app_user.app_user_id]
  error_message text
}

//// ========================================================
//// 5. DATA GOVERNANCE (CATALOG, LINEAGE, QUALITY)
//// ========================================================

Table catalog_table {
  catalog_table_id bigint [pk, increment]
  dataset_id bigint [not null, ref: > dataset.dataset_id]
  table_name varchar(150) [not null]
  source_table_name varchar(150)
  description text
  owner varchar(150)
  created_at timestamp [not null, default: `now()` ]
  updated_at timestamp

  indexes {
    (dataset_id, table_name) [unique]
  }
}

Table catalog_column {
  catalog_column_id bigint [pk, increment]
  catalog_table_id bigint [not null, ref: > catalog_table.catalog_table_id]
  column_name varchar(150) [not null]
  data_type varchar(50) [not null]
  description text
  is_nullable boolean
  is_primary_key boolean [not null, default: false]
  is_foreign_key boolean [not null, default: false]

  indexes {
    (catalog_table_id, column_name) [unique]
  }
  Note: 'Cung cap context semantic cho Schema Retrieval & Text-to-SQL'
}

Table data_lineage {
  data_lineage_id bigint [pk, increment]
  transformation_step_id bigint [not null, ref: > transformation_step.transformation_step_id]
  source_column_id bigint [not null, ref: > catalog_column.catalog_column_id]
  target_column_id bigint [not null, ref: > catalog_column.catalog_column_id]
  lineage_type varchar(50) [not null, note: 'DIRECT_COPY, DERIVED, AGGREGATED']
  transformation_expression text
  created_at timestamp [not null, default: `now()` ]
}

Table data_quality_rule {
  data_quality_rule_id bigint [pk, increment]
  catalog_column_id bigint [not null, ref: > catalog_column.catalog_column_id]
  rule_code varchar(50) [unique, not null]
  rule_name varchar(150) [not null]
  rule_type varchar(50) [not null, note: 'COMPLETENESS, VALIDITY, UNIQUENESS, CONSISTENCY, DATA_TYPE']
  rule_expression text [not null]
  severity varchar(20) [not null, default: 'HIGH', note: 'LOW, MEDIUM, HIGH, CRITICAL']
  is_active boolean [not null, default: true]
}

Table data_quality_run {
  data_quality_run_id bigint [pk, increment]
  data_quality_rule_id bigint [not null, ref: > data_quality_rule.data_quality_rule_id]
  ingestion_job_id bigint [ref: > ingestion_job.ingestion_job_id]
  run_at timestamp [not null, default: `now()` ]
  total_records bigint [not null]
  passed_records bigint [not null]
  failed_records bigint [not null]
  pass_rate decimal(5,2) [not null]
  execution_status varchar(30) [not null, note: 'PASSED, FAILED, WARNING']
  failure_sample_ref text
}

//// ========================================================
//// 6. AI ANALYTICS & TEXT-TO-SQL
//// ========================================================

Table ai_model {
  ai_model_id bigint [pk, increment]
  provider varchar(100) [not null, note: 'OpenAI, Anthropic, Ollama']
  model_name varchar(150) [not null, note: 'gpt-4o, claude-3-5-sonnet']
  model_version varchar(100)
  is_active boolean [not null, default: true]
  created_at timestamp [not null, default: `now()` ]
}

Table prompt_template {
  prompt_template_id bigint [pk, increment]
  template_name varchar(150) [not null]
  version varchar(30) [not null]
  template_text text [not null]
  is_active boolean [not null, default: true]
  created_at timestamp [not null, default: `now()` ]

  indexes {
    (template_name, version) [unique]
  }
}

Table query_request {
  query_request_id bigint [pk, increment]
  app_user_id bigint [not null, ref: > app_user.app_user_id]
  question_text text [not null]
  language varchar(20) [default: 'vi']
  detected_intent varchar(150)
  submitted_at timestamp [not null, default: `now()` ]
  status varchar(30) [not null, note: 'PENDING, PROCESSING, COMPLETED, FAILED']
}

Table schema_retrieval {
  schema_retrieval_id bigint [pk, increment]
  query_request_id bigint [not null, ref: > query_request.query_request_id]
  catalog_table_id bigint [not null, ref: > catalog_table.catalog_table_id]
  retrieval_rank int [not null]
  relevance_score decimal(6,4)

  indexes {
    (query_request_id, catalog_table_id) [unique]
  }
}

Table generated_sql {
  generated_sql_id bigint [pk, increment]
  query_request_id bigint [not null, ref: > query_request.query_request_id]
  ai_model_id bigint [not null, ref: > ai_model.ai_model_id]
  prompt_template_id bigint [not null, ref: > prompt_template.prompt_template_id]
  sql_text text [not null]
  generated_at timestamp [not null, default: `now()` ]
  validation_status varchar(30) [not null, note: 'PENDING, VALID, SYNTAX_ERROR, RESTRICTED']
  validation_error text
}

Table query_execution {
  query_execution_id bigint [pk, increment]
  generated_sql_id bigint [unique, not null, ref: > generated_sql.generated_sql_id]
  executed_at timestamp [not null, default: `now()` ]
  execution_status varchar(30) [not null, note: 'SUCCESS, FAILED, TIMEOUT']
  row_count bigint
  execution_time_ms int
  result_ref text
  error_message text
}

//// ========================================================
//// 7. SECURITY & RBAC
//// ========================================================

Table app_user {
  app_user_id bigint [pk, increment]
  username varchar(100) [unique, not null]
  password_hash text [not null]
  employee_id bigint [unique, ref: > employee.employee_id]
  is_active boolean [not null, default: true]
  created_at timestamp [not null, default: `now()` ]
}

Table role {
  role_id bigint [pk, increment]
  role_code varchar(50) [unique, not null, note: 'ADMIN, DATA_ADMIN, ANALYST, VIEWER']
  role_name varchar(100) [not null]
  description text
}

Table permission {
  permission_id bigint [pk, increment]
  permission_code varchar(100) [unique, not null, note: 'CATALOG_READ, QUERY_EXECUTE, USER_MANAGE']
  permission_name varchar(150) [not null]
  description text
}

Table user_role {
  app_user_id bigint [ref: > app_user.app_user_id]
  role_id bigint [ref: > role.role_id]

  indexes {
    (app_user_id, role_id) [pk]
  }
}

Table role_permission {
  role_id bigint [ref: > role.role_id]
  permission_id bigint [ref: > permission.permission_id]

  indexes {
    (role_id, permission_id) [pk]
  }
}

Table login_session {
  login_session_id bigint [pk, increment]
  app_user_id bigint [not null, ref: > app_user.app_user_id]
  login_at timestamp [not null, default: `now()` ]
  logout_at timestamp
  ip_address varchar(45)
  session_status varchar(30) [not null, note: 'ACTIVE, EXPIRED, TERMINATED']
}

//// ========================================================
//// 8. DASHBOARD & VISUALIZATION
//// ========================================================

Table dashboard {
  dashboard_id bigint [pk, increment]
  dashboard_name varchar(150) [not null]
  owner_app_user_id bigint [not null, ref: > app_user.app_user_id]
  description text
  created_at timestamp [not null, default: `now()` ]
  updated_at timestamp
}

Table kpi {
  kpi_id bigint [pk, increment]
  kpi_code varchar(50) [unique, not null]
  kpi_name varchar(150) [not null]
  domain varchar(50) [not null, note: 'Admissions, Academic, HR']
  unit varchar(50)
  description text
}

Table kpi_value {
  kpi_value_id bigint [pk, increment]
  kpi_id bigint [not null, ref: > kpi.kpi_id]
  period_date date [not null]
  kpi_value decimal(20,4) [not null]
  dimension_json text [note: 'JSON string/PostgreSQL JSONB de filter theo department/major/year']
  created_at timestamp [not null, default: `now()` ]

  indexes {
    (kpi_id, period_date)
  }
}

Table chart_widget {
  chart_widget_id bigint [pk, increment]
  dashboard_id bigint [not null, ref: > dashboard.dashboard_id]
  widget_type varchar(50) [not null, note: 'KPI_CARD, BAR_CHART, LINE_CHART, PIE_CHART, TABLE']
  title varchar(200) [not null]
  query_request_id bigint [ref: > query_request.query_request_id]
  kpi_id bigint [ref: > kpi.kpi_id]
  config_json text [note: 'Options cho Recharts: xKey, yKey, colors...']
  position_x int
  position_y int
  width int
  height int
}

DiagramView Default {
  Schemas {
    public
  }
  Notes { * }
}
