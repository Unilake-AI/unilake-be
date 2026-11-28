# Tài liệu Đặc tả Nghiệp vụ & Chi tiết API Phân hệ DATA_ADMIN (UniLake AI)

Tài liệu này tổng hợp toàn bộ vai trò **DATA_ADMIN (Quản trị viên Dữ liệu / Data Engineer / Data Steward)**, quy trình điều hành dữ liệu đầu-cuối (End-to-End Data Pipeline), ranh giới trách nhiệm, và chi tiết đặc tả kỹ thuật toàn bộ API (kèm cấu trúc **Request**, **Response** và **Mã lỗi**) trên nền tảng UniLake AI.

- **Môi trường / Base URL:** `http://localhost:8000/api/v1`
- **Xác thực:** Bearer Token trong Header `Authorization: Bearer <access_token>`
- **Tracing Header:** `X-Request-ID` (Backend tự sinh trong mỗi request để truy vết sự cố)

---

## 1. Vai trò và Ranh giới Trách nhiệm của DATA_ADMIN

`DATA_ADMIN` là vai trò "nhạc trưởng" kỹ thuật điều hành toàn bộ nền tảng Lakehouse:

| Nhiệm vụ chính | Hành vi nghiệp vụ cụ thể | Đối tượng tương tác |
| :--- | :--- | :--- |
| **Quản trị Kết nối Nguồn** | Khai báo Hệ thống nguồn (`source-systems`) và Nguồn dữ liệu (`datasources`: PostgreSQL, S3, API...). Kiểm tra thông tuyến kết nối. | Hệ thống nguồn bên ngoài, Cơ sở dữ liệu trường. |
| **Quy hoạch Data Catalog** | Tạo các Bộ dữ liệu (`datasets`), xác định thuộc tầng (`BRONZE`), lĩnh vực (`domain`), và bàn giao cho phòng ban sở hữu (`owner_department_id`). | Manager các phòng ban (`PNS`, `PDT`, `PTS`). |
| **Điều phối Thu thập (Ingestion)** | Lập lịch/chạy thu thập dữ liệu tự động từ DB nguồn vào Bronze; giám sát worker, xử lý retry khi job lỗi. | Ingestion Workers, MinIO Bronze. |
| **Thiết kế Quy tắc Chuẩn hóa** | Soạn thảo các **Silver Profile** (Mapping cột nguồn $\rightarrow$ đích, luật validate format, xử lý missing, gộp trùng lặp). Đệ trình Manager duyệt. | DuckDB Engine, Unified Schema, Manager (Duyệt). |
| **Vận hành Cleaning Engine** | Chạy thử nghiệm (`preview`) và kích hoạt chính thức tiến trình Làm sạch (`Cleaning Pipeline`) từ Bronze $\rightarrow$ Silver Snapshots. | DuckDB Cleaning Engine, Parquet Store. |
| **Kiểm soát Cách ly & Sửa lỗi** | Phân tích các dòng bị cách ly (`quarantine_records`), đọc dữ liệu thô Bronze, gom bản ghi lỗi để tạo **Issue** giao về cho Staff sửa, thẩm định kết quả (`verification`). | Bảng `quarantine_record`, Nhân viên `STAFF`. |
| **Quản trị Tầng Silver & Gold** | Giám sát các phiên bản Snapshot Silver, kiểm toán Lineage toàn trường, điều phối xuất bản Gold. | Data Catalog, Ban Giám hiệu (`VIEWER`). |

---

## 2. Sơ đồ Luồng Nghiệp vụ Điều hành của DATA_ADMIN

```text
========================================================================================
LUỒNG 1: KHAI BÁO NGUỒN DỮ LIỆU & QUY HOẠCH CATALOG
========================================================================================
  [Data Admin] ──► POST /api/v1/source-systems ──► Khai báo hệ thống (VD: CSDL Đào tạo)
  [Data Admin] ──► POST /api/v1/datasources/create ──► Cấu hình kết nối DB, kiểm tra test-connection
  [Data Admin] ──► POST /api/v1/datasets ────────► Tạo Bộ dữ liệu Bronze & gán phòng ban sở hữu

========================================================================================
LUỒNG 2: ĐIỀU PHỐI NẠP & GIÁM SÁT WORKER
========================================================================================
  [Data Admin] ──► POST /api/v1/ingestion/jobs ──► Kích hoạt thu thập dữ liệu từ nguồn DB
  [Data Admin] ──► GET /api/v1/ingestion/workers ─► Giám sát trạng thái, heartbeat, lease của worker
  [Data Admin] ──► POST /api/v1/ingestion/jobs/{id}/retry ──► Chạy lại các job bị lỗi hạ tầng

========================================================================================
LUỒNG 3: CẤU HÌNH QUY TẮC SILVER & KÍCH HOẠT LÀM SẠCH (BRONZE -> SILVER)
========================================================================================
  1. [Data Admin] ──► POST /api/v1/pipeline/profiles ──────► Soạn thảo Profile (Mapping, Rules)
  2. [Manager]    ──► Duyệt quy tắc tại /pipeline/profiles/{id}/approve (Domain Owner Gate)
  3. [Data Admin] ──► POST /api/v1/pipeline/cleaning/ingestion-jobs/{id}/preview (Chạy thử 100 dòng)
  4. [Data Admin] ──► POST /api/v1/pipeline/cleaning/ingestion-jobs/{id} (Chạy chính thức)
                      ├── Dữ liệu hợp lệ ──► Xuất bản thành Silver Snapshot
                      └── Dữ liệu lỗi     ──► Đưa vào bảng app.quarantine_record

========================================================================================
LUỒNG 4: XỬ LÝ HỒ SƠ CÁCH LY, GIAO VIỆC & THẨM ĐỊNH (DATA QUALITY REMEDIATION)
========================================================================================
  1. [Data Admin] ──► GET /api/v1/quarantine-records ──────► Quét danh sách các dòng lỗi
  2. [Data Admin] ──► GET /api/v1/quarantine-records/{id}/source ──► Đọc dòng thô đối chiếu
  3. [Data Admin] ──► GET /api/v1/data-quality-issues/assignees ──► Chọn nhân viên nhận việc
  4. [Data Admin] ──► POST /api/v1/data-quality-issues ────► Gom 1-100 bản ghi lỗi, tạo Issue (ASSIGNED)
  5. [Staff]      ──► Sửa dữ liệu, nộp bản sửa ────────────► Issue chuyển PENDING_VERIFICATION
  6. [Data Admin] ──► GET /api/v1/data-quality-issues/{id}/comparison ──► Đối chiếu trước & sau sửa
  7. [Data Admin] ──► POST /api/v1/data-quality-issues/{id}/verification
                      ├── ĐẠT (verified: true)   ──► VERIFIED, xuất bản dữ liệu sạch lên Silver
                      └── HỎNG (verified: false) ──► Quay lại ASSIGNED (Staff phải sửa lại file mới)
```

---

## 3. Đặc tả Chi tiết từng API của DATA_ADMIN

---

### NHÓM 1: QUẢN TRỊ NGUỒN DỮ LIỆU & HỆ THỐNG NGUỒN

#### 1. Đăng ký Hệ thống nguồn mới
* **Endpoint:** `POST /api/v1/source-systems`
* **Quyền hạn:** `ADMIN`, `DATA_ADMIN`
* **Mục đích:** Khai báo hệ thống phần mềm nghiệp vụ gốc trong trường.

**Request Body (`application/json`):**
```json
{
  "source_code": "SIS_PORTAL",
  "source_name": "Hệ thống Quản lý Đào tạo UIS",
  "source_type": "DATABASE",
  "description": "Cơ sở dữ liệu Oracle quản lý sinh viên và điểm thi"
}
```

**Response `201 Created`:**
```json
{
  "source_system_id": 3,
  "source_code": "SIS_PORTAL",
  "source_name": "Hệ thống Quản lý Đào tạo UIS",
  "source_type": "DATABASE",
  "is_active": true,
  "created_at": "2026-11-18T08:00:00Z"
}
```

---

#### 2. Khai báo Nguồn dữ liệu kết nối
* **Endpoint:** `POST /api/v1/datasources/create`
* **Quyền hạn:** `ADMIN`, `DATA_ADMIN`
* **Mục đích:** Cấu hình thông số kết nối (Host, Port, DB, User, Password hoặc S3/MinIO bucket).

**Request Body (`application/json`):**
```json
{
  "source_system_id": 3,
  "source_name": "Postgres Core DB",
  "source_type": "POSTGRESQL",
  "connection_type": "DATABASE",
  "configuration": {
    "host": "postgres-core.university.edu.vn",
    "port": 5432,
    "database": "student_records",
    "username": "unilake_reader",
    "password": "SecretPassword@123"
  }
}
```

**Response `201 Created`:**
```json
{
  "data_source_id": 8,
  "source_name": "Postgres Core DB",
  "source_type": "POSTGRESQL",
  "is_active": true,
  "created_at": "2026-11-18T08:30:00Z"
}
```

---

#### 3. Kiểm tra kết nối nguồn (Test Connection)
* **Endpoint:** `POST /api/v1/datasources/test-connection`
* **Request:** Gửi thông tin cấu hình `configuration`.
* **Response `200 OK`:** `{"success": true, "message": "Kết nối thành công tới máy chủ cơ sở dữ liệu."}`  
* **Mã lỗi:** `422 CONNECTION_FAILED` (Nếu sai mật khẩu, sai IP hoặc timeout).

---

### NHÓM 2: QUY HOẠCH BỘ DỮ LIỆU & PHÂN BỔ PHÒNG BAN

#### 4. Tạo Bộ dữ liệu mới trong Catalog
* **Endpoint:** `POST /api/v1/datasets`
* **Quyền hạn:** `ADMIN`, `DATA_ADMIN`
* **Mục đích:** Tạo bộ dữ liệu Bronze và bàn giao cho phòng ban sở hữu để Staff nạp dữ liệu.

**Request Body (`application/json`):**
```json
{
  "dataset_code": "hr_employees_master",
  "dataset_name": "Hồ sơ Nhân sự Toàn trường 2026",
  "domain": "hr",
  "source_system_id": 1,
  "description": "Bộ dữ liệu gốc về cán bộ giảng viên, hợp đồng và quá trình công tác"
}
```

**Response `201 Created`:**
```json
{
  "dataset_id": 12,
  "dataset_code": "hr_employees_master",
  "dataset_name": "Hồ sơ Nhân sự Toàn trường 2026",
  "domain": "hr",
  "status": "ACTIVE",
  "created_at": "2026-11-18T09:00:00Z"
}
```

---

### NHÓM 3: ĐIỀU PHỐI NẠP & GIÁM SÁT WORKER

#### 5. Kích hoạt Job thu thập từ Nguồn đã đăng ký (DB/Storage Ingestion)
* **Endpoint:** `POST /api/v1/ingestion/jobs`
* **Quyền hạn:** `ADMIN`, `DATA_ADMIN`
* **Mục đích:** Thu thập dữ liệu theo lịch hoặc thủ công từ database nguồn vào Bronze MinIO.

**Request Body (`application/json`):**
```json
{
  "data_source_id": 8,
  "dataset_id": 12,
  "source_table": "public.employees",
  "validation_policy": {
    "policy_id": "hr-db-ingest-v1",
    "version": "1"
  }
}
```

**Response `202 Accepted`:**
```json
{
  "ingestion_job_id": 204,
  "data_source_id": 8,
  "dataset_id": 12,
  "status": "QUEUED",
  "stage": "QUEUED",
  "job_type": "DATABASE_V1",
  "source_table": "public.employees",
  "created_at": "2026-11-18T09:15:00Z"
}
```

---

#### 6. Giám sát tình trạng các Ingestion Worker
* **Endpoint:** `GET /api/v1/ingestion/workers`
* **Quyền hạn:** `ADMIN`, `DATA_ADMIN`
* **Mục đích:** Kiểm tra worker còn sống (heartbeat), đang xử lý job nào, có bị nghẽn queue không.

**Response `200 OK`:**
```json
[
  {
    "worker_id": "worker-pod-01",
    "status": "HEALTHY",
    "active_job_id": 204,
    "last_heartbeat": "2026-11-18T09:15:30Z",
    "uptime_seconds": 86400
  }
]
```

---

#### 7. Kích hoạt chạy lại Job thất bại (Retry Job)
* **Endpoint:** `POST /api/v1/ingestion/jobs/{job_id}/retry`
* **Quyền hạn:** `ADMIN`, `DATA_ADMIN`
* **Response `202 Accepted`:** Sinh lượt attempt mới cho job.

---

### NHÓM 4: CẤU HÌNH QUY TẮC SILVER & CHẠY CLEANING PIPELINE

#### 8. Tạo / Cập nhật Cấu hình làm sạch Silver (Profile Configuration)
* **Endpoint:** `POST /api/v1/pipeline/profiles`
* **Quyền hạn:** `ADMIN`, `DATA_ADMIN`
* **Mục đích:** Định nghĩa quy tắc bóc tách dữ liệu từ file thô Bronze sang bảng Silver: luật missing, định dạng ngày/CCCD/email, quy tắc gộp trùng lặp (`collapse_exact_primary_key_duplicates`).

**Request Body (`application/json`):**
```json
{
  "profile_id": "hr_employees_clean_v1",
  "domain": "hr",
  "entity": "employee",
  "target_schema_version": "v1",
  "description": "Làm sạch hồ sơ cán bộ: loại bỏ khoảng trắng, format CCCD 12 số, gộp trùng dòng tuyệt đối",
  "mapping_rules": [
    {
      "source_column": "Mã cán bộ",
      "target_field": "employee_code",
      "required": true
    },
    {
      "source_column": "Họ và tên",
      "target_field": "full_name",
      "required": true
    },
    {
      "source_column": "Ngày sinh",
      "target_field": "date_of_birth",
      "format": "YYYY-MM-DD"
    }
  ],
  "collapse_exact_primary_key_duplicates": true
}
```

**Response `201 Created`:** Sinh `revision` mới (ví dụ: `rev-001`) và đưa vào Hàng đợi duyệt của Trưởng phòng Nhân sự.

---

#### 9. Chạy thử nghiệm làm sạch (Cleaning Preview)
* **Endpoint:** `POST /api/v1/pipeline/cleaning/ingestion-jobs/{ingestion_job_id}/preview`
* **Quyền hạn:** `ADMIN`, `DATA_ADMIN`
* **Mục đích:** Chạy thử thuật toán làm sạch trên tối đa 100 dòng đầu tiên của file Bronze. **Không ghi dữ liệu vào Silver, không lưu hồ sơ lỗi**, giúp Data Admin thẩm định trước độ chính xác của Profile.

**Request Body (`application/json`):**
```json
{
  "profile_id": "hr_employees_clean_v1"
}
```

**Response `200 OK`:**
```json
{
  "job_id": 105,
  "profile_id": "hr_employees_clean_v1",
  "total_previewed": 100,
  "valid_count": 92,
  "invalid_count": 8,
  "preview_valid_sample": [
    {
      "employee_code": "NV001",
      "full_name": "Nguyễn Văn A",
      "date_of_birth": "1985-05-12"
    }
  ],
  "preview_errors_sample": [
    {
      "row_locator": "hr_employees_q4.csv:row:18",
      "issues": ["REQUIRED_VALUE_MISSING:employee_code", "FORMAT_DATE_INVALID:date_of_birth"]
    }
  ]
}
```

---

#### 10. Kích hoạt chạy Cleaning chính thức & Xuất bản Silver
* **Endpoint:** `POST /api/v1/pipeline/cleaning/ingestion-jobs/{ingestion_job_id}`
* **Quyền hạn:** `ADMIN`, `DATA_ADMIN`
* **Mục đích:** DuckDB Cleaning Engine quét toàn bộ mẻ nạp Bronze: dòng sạch xuất bản thành Silver Snapshot; dòng lỗi tự động tách vào bảng cách ly `app.quarantine_record`.

**Request Body (`application/json`):**
```json
{
  "profile_id": "hr_employees_clean_v1",
  "base_snapshot": null,
  "allow_partial_publication": true
}
```

**Response `200 OK`:**
```json
{
  "cleaning_run_id": "11223344-5566-7788-99aa-bbccddeeff00",
  "status": "COMPLETED",
  "records_read": 350,
  "records_clean": 342,
  "records_quarantined": 8,
  "silver_snapshot_id": "snap-silver-hr-001",
  "quarantine_report_ref": "s3://silver/quarantine/report-11223344.json",
  "executed_at": "2026-11-18T10:00:00Z"
}
```

---

### NHÓM 5: HỒ SƠ CÁCH LY (QUARANTINE) & QUẢN LÝ VÒNG ĐỜI LỖI

#### 11. Danh sách Hồ sơ cách ly (Quarantine Records)
* **Endpoint:** `GET /api/v1/quarantine-records`
* **Quyền hạn:** `ADMIN`, `DATA_ADMIN`
* **Mục đích:** Tra cứu, lọc danh mục tất cả các dòng dữ liệu bị lỗi cách ly trên toàn hệ thống.

**Query Parameters:**
| Tham số | Kiểu | Mô tả |
| :--- | :---: | :--- |
| `cleaning_run_id` | UUID | Lọc theo đợt chạy Cleaning |
| `ingestion_job_id` | integer | Lọc theo lần nạp tệp |
| `dataset_id` | integer | Lọc theo bộ dữ liệu |
| `owner_department_id`| integer | Lọc theo phòng ban sở hữu |
| `error_code` | string | Lọc theo mã lỗi (VD: `REQUIRED_VALUE_MISSING`, `DUPLICATE_KEY_COLLISION`) |
| `limit` / `offset` | integer | Phân trang (mặc định 20 / 0) |

**Response `200 OK`:**
```json
[
  {
    "record_id": "aa11bb22-cc33-dd44-ee55-ff6677889900",
    "cleaning_run_id": "11223344-5566-7788-99aa-bbccddeeff00",
    "ingestion_job_id": 105,
    "dataset_id": 5,
    "owner_department_id": 1,
    "locator": "hr_employees_q4.csv:row:18",
    "record_position": 17,
    "bronze_version": "v1",
    "source_table": "hr_employees_q4.csv",
    "entity": "employee",
    "error_codes": ["REQUIRED_VALUE_MISSING", "FORMAT_DATE_INVALID"],
    "created_at": "2026-11-18T10:00:05Z"
  }
]
```

---

#### 12. Đọc dòng dữ liệu thô gốc từ Bronze (Xem nội dung dòng lỗi)
* **Endpoint:** `GET /api/v1/quarantine-records/{record_id}/source`
* **Quyền hạn:** `ADMIN`, `DATA_ADMIN`
* **Mục đích:** Backend sử dụng tài khoản chỉ đọc, truy xuất thẳng vào file Bronze MinIO theo đúng phiên bản và vị trí `locator` để trả về giá trị thô nguyên bản của dòng bị lỗi.

**Response `200 OK`:**
```json
{
  "record_id": "aa11bb22-cc33-dd44-ee55-ff6677889900",
  "source_table": "hr_employees_q4.csv",
  "row_number": 18,
  "raw_values": {
    "Mã cán bộ": "",
    "Họ và tên": "Trần Thị B",
    "Ngày sinh": "31/02/1990",
    "Phòng ban": "PNS"
  },
  "violated_fields": ["Mã cán bộ", "Ngày sinh"]
}
```

---

#### 13. Danh sách nhân viên đủ điều kiện nhận việc (Assignees)
* **Endpoint:** `GET /api/v1/data-quality-issues/assignees`
* **Query Parameters:** `department_id=1&domain=hr`
* **Mục đích:** Data Admin chọn nhân viên phòng ban đang `ACTIVE` và đúng chuyên môn để giao việc sửa file.

**Response `200 OK`:**
```json
[
  {
    "user_id": 12,
    "username": "hr_staff",
    "full_name": "Lê Văn C",
    "department_id": 1,
    "department_name": "Phòng Tổ chức Nhân sự"
  }
]
```

---

#### 14. Tạo Yêu cầu Sửa lỗi Dữ liệu (Issue Creation)
* **Endpoint:** `POST /api/v1/data-quality-issues`
* **Quyền hạn:** `ADMIN`, `DATA_ADMIN`
* **Mục đích:** Data Admin gom từ 1 đến 100 bản ghi lỗi trong bảng cách ly có cùng file/dataset, tạo thành Issue và giao cho nhân viên phụ trách. Trạng thái sinh ra là **`ASSIGNED`**.

**Request Body (`application/json`):**
```json
{
  "title": "Thiếu mã cán bộ và sai ngày sinh trong mẻ nạp Q4",
  "description": "Yêu cầu rà soát hồ sơ bản cứng, cập nhật lại mã nhân viên và ngày sinh chuẩn YYYY-MM-DD.",
  "department_id": 1,
  "assignee_user_id": 12,
  "quarantine_record_ids": [
    "aa11bb22-cc33-dd44-ee55-ff6677889900",
    "bb22cc33-dd44-ee55-ff66-001122334455"
  ]
}
```

**Response `201 Created`:** Trả về đối tượng `IssueDetail` với trạng thái `status: "ASSIGNED"`.

---

#### 15. Đối chiếu dữ liệu trước & sau khi nhân viên sửa (Issue Comparison)
* **Endpoint:** `GET /api/v1/data-quality-issues/{issue_id}/comparison`
* **Quyền hạn:** `ADMIN`, `DATA_ADMIN`
* **Mục đích:** So sánh tự động từng dòng lỗi cũ với dòng mới nạp theo khóa nghiệp vụ (`employee_code`), hiển thị rõ trường nào đã sửa đúng, trường nào vẫn còn sai.

**Response `200 OK`:**
```json
{
  "issue_id": "7b8e1a3c-5d2f-4e9b-8c11-9876543210ab",
  "total_quarantined": 2,
  "total_remediated": 2,
  "total_unresolved": 0,
  "details": [
    {
      "record_id": "aa11bb22-cc33-dd44-ee55-ff6677889900",
      "old_values": {"employee_code": "", "date_of_birth": "31/02/1990"},
      "new_values": {"employee_code": "NV0045", "date_of_birth": "1990-02-28"},
      "is_valid": true
    }
  ]
}
```

---

#### 16. Thẩm định & Đóng vòng đời lỗi (Verification)
* **Endpoint:** `POST /api/v1/data-quality-issues/{issue_id}/verification`
* **Quyền hạn:** `ADMIN`, `DATA_ADMIN`
* **Mục đích:** Chốt kết quả xử lý của nhân viên.
  * Nếu **ĐẠT (`verified: true`)**: Đóng Issue (`VERIFIED`), dữ liệu sạch được xuất bản bổ sung vào Silver.
  * Nếu **KHÔNG ĐẠT (`verified: false`)**: Trả Issue về trạng thái `ASSIGNED` kèm ghi chú lý do từ chối để nhân viên làm lại.

**Request Body (`application/json`) - Khi chấp thuận:**
```json
{
  "cleaning_run_id": "22334455-6677-8899-aabb-ccddeeff0011",
  "verified": true,
  "note": "Đã kiểm tra đối chiếu đầy đủ với hồ sơ lưu trữ. Đạt tiêu chuẩn.",
  "confirmed_record_ids": [
    "aa11bb22-cc33-dd44-ee55-ff6677889900",
    "bb22cc33-dd44-ee55-ff66-001122334455"
  ]
}
```

**Request Body (`application/json`) - Khi từ chối:**
```json
{
  "cleaning_run_id": "22334455-6677-8899-aabb-ccddeeff0011",
  "verified": false,
  "note": "Dòng 18 mã nhân viên bị trùng với cán bộ đã nghỉ việc năm 2024. Vui lòng cấp mã mới."
}
```

**Response `200 OK`:** Cập nhật trạng thái `status: "VERIFIED"` (nếu đạt) hoặc `status: "ASSIGNED"` (nếu bị từ chối) và ghi lịch sử sự kiện `history`.

---

### NHÓM 6: QUẢN TRỊ SILVER SNAPSHOTS & DATA CATALOG TOÀN TRƯỜNG

#### 17. Danh mục phiên bản Silver Snapshots
* **Endpoint:** `GET /api/v1/pipeline/silver/snapshots`
* **Quyền hạn:** `ADMIN`, `DATA_ADMIN`
* **Mục đích:** Quản lý các mốc bản chụp dữ liệu sạch (Snapshots) của từng domain, phục vụ kiểm toán và rollback khi cần.

**Response `200 OK`:**
```json
[
  {
    "snapshot_id": "snap-silver-hr-001",
    "scope": "production:pns-hr",
    "parent_snapshot": null,
    "status": "PUBLISHED",
    "created_at": "2026-11-18T10:00:00Z",
    "entities": ["employee", "department"]
  }
]
```

---

#### 18. Xem Data Catalog toàn hệ thống (Governance Catalog)
* **Endpoint:** `GET /api/v1/governance/catalog`
* **Quyền hạn:** `DATA_ADMIN`
* **Mục đích:** Tra cứu toàn bộ bảng, cột, kiểu dữ liệu, quan hệ khóa ngoại (ERD) và metadata của toàn bộ các lĩnh vực trong trường.

---

## 4. Bảng Tra cứu Mã Lỗi Nghiệp vụ Phổ biến cho DATA_ADMIN

| HTTP Status | Mã lỗi Backend (`error.code`) | Ý nghĩa & Nguyên nhân | Hướng xử lý cho Data Admin |
| :---: | :--- | :--- | :--- |
| `401` | `UNAUTHORIZED` | Token hết hạn hoặc chưa đăng nhập. | Đăng nhập lại tài khoản `dataadmin`. |
| `403` | `FORBIDDEN` | Tài khoản không có vai trò `DATA_ADMIN`. | Đăng nhập đúng tài khoản có quyền quản trị dữ liệu. |
| `404` | `SILVER_SOURCE_PROFILE_NOT_FOUND` | Profile làm sạch chỉ định không tồn tại. | Kiểm tra lại `profile_id`. |
| `409` | `SUCCESSFUL_BRONZE_JOB_REQUIRED` | Bấm chạy Cleaning trên Job Bronze bị lỗi hoặc đang chạy. | Chỉ chạy Cleaning khi Ingestion Job đã `SUCCESS`. |
| `422` | `SOURCE_LAYOUT_MISMATCH` | Cột trong file Bronze không khớp với quy tắc Mapping trong Profile. | Cập nhật lại Profile hoặc yêu cầu nạp đúng định dạng cột. |
| `422` | `SNAPSHOT_REJECTED` | Dữ liệu sau khi làm sạch vẫn vi phạm ràng buộc toàn vẹn Silver (ví dụ trùng Primary Key chéo). | Kiểm tra lại báo cáo validation Silver. |
| `422` | `PARTIAL_PUBLICATION_NOT_ALLOWED` | Chạy xuất bản một phần nhưng không có dòng nào sạch. | Sửa lỗi file trước khi nạp lại. |
| `503` | `PIPELINE_OBJECT_STORAGE_UNAVAILABLE` | Mất kết nối MinIO lưu trữ Bronze/Silver. | Kiểm tra hạ tầng Docker MinIO bucket. |

---

## 5. Tài khoản Kiểm thử Mẫu (Seed Data)

* Mật khẩu mặc định: **`Password@123`**

| Tài khoản | Vai trò (`role`) | Quyền hạn đặc trưng |
| :--- | :--- | :--- |
| **`dataadmin`** | `DATA_ADMIN` | Quản lý toàn bộ nguồn, dataset, worker, cleaning pipeline, cách ly, duyệt issue và catalog toàn trường. |
| **`admin`** | `ADMIN` | Quản trị tối cao (hệ thống, tài khoản, phân quyền và pipeline). |
