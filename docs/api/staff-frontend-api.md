# Tài liệu API dành cho Frontend — Luồng STAFF

Tài liệu này tổng hợp đầy đủ các API phục vụ Frontend xây dựng UI và tích hợp cho nhóm quyền **STAFF** (`ADMISSIONS_STAFF`, `ACADEMIC_STAFF`, `HR_STAFF`), kèm mô tả chức năng, tham số Request và cấu trúc Response thực tế từ Backend.

- **API Prefix mặc định:** `/api/v1` (trừ `/` và `/health`)
- **Xác thực:** Bearer JWT qua header `Authorization: Bearer <access_token>`
- **Response Header hữu ích:** `X-Request-ID` (hệ thống tự gắn để trace log khi có lỗi)

---

## Mục lục

1. [Tổng quan phân quyền & quy ước chung](#1-tổng-quan-phân-quyền--quy-ước-chung)
2. [Nhóm API Ngữ cảnh màn hình STAFF](#2-nhóm-api-ngữ-cảnh-màn-hình-staff)
3. [Nhóm API Bộ dữ liệu Bronze (Datasets)](#3-nhóm-api-bộ-dữ-liệu-bronze-datasets)
4. [Nhóm API Upload & Theo dõi Ingestion](#4-nhóm-api-upload--theo-dõi-ingestion)
5. [Nhóm API Xử lý yêu cầu dữ liệu lỗi (Data Quality Issues)](#5-nhóm-api-xử-lý-yêu-cầu-dữ-liệu-lỗi-data-quality-issues)
6. [API Hệ thống & Dùng chung khác](#6-api-hệ-thống--dùng-chung-khác)

---

## 1. Tổng quan phân quyền & quy ước chung

### 1.1. Phạm vi của 3 role STAFF

| Role | Mã lĩnh vực (`domain`) | Tên hiển thị |
| --- | --- | --- |
| `ADMISSIONS_STAFF` | `admissions` | Tuyển sinh |
| `ACADEMIC_STAFF` | `academic` | Đào tạo |
| `HR_STAFF` | `hr` | Nhân sự |

- **Upload & theo dõi ingestion:** Giới hạn theo `domain` của role STAFF.
- **Yêu cầu sửa dữ liệu lỗi (`data-quality-issues`):** Ngoài đúng `domain`, tài khoản STAFF phải có hồ sơ nhân sự và phân công phòng ban đang `ACTIVE` trong DB. Chỉ đúng người được giao (`assignee_user_id`) mới được upload bản sửa và gửi báo cáo đã sửa (`resolution`).
- **Thao tác KHÔNG thuộc quyền STAFF:** Tạo/sửa `datasources`, tạo `source-systems`, tạo `datasets` qua `/datasets`, `POST /ingestion/jobs`, `retry` job, xem `workers`, chạy Cleaning/Preview Silver, quản lý `profiles`/`snapshots`/`quarantine-records`, tạo/giao/xác minh `data-quality-issues`, `governance`.

### 1.2. Luồng gọi API gợi ý cho UI

```text
1. (Sau khi đăng nhập Auth) GET /api/v1/staff/context -> Lấy quyền, domain, phòng ban, giới hạn file
2. Luồng Upload dữ liệu mới / bổ sung:
   - GET  /api/v1/datasets              -> Chọn dataset có sẵn (hoặc bỏ qua nếu tạo mới)
   - POST /api/v1/ingestion/uploads     -> Upload file, nhận ingestion_job_id (202)
   - GET  /api/v1/ingestion/jobs/{id}   -> Polling trạng thái đến SUCCESS / FAILED / REJECTED
3. Luồng Xử lý yêu cầu sửa lỗi:
   - GET  /api/v1/data-quality-issues?mine_only=true&status=ASSIGNED
   - GET  /api/v1/data-quality-issues/{issue_id}            -> Xem chi tiết dòng lỗi & tên file cần sửa
   - POST /api/v1/data-quality-issues/{issue_id}/uploads    -> Upload file đã sửa, nhận ingestion_job_id (202)
   - GET  /api/v1/ingestion/jobs/{id}                       -> Polling chờ SUCCESS
   - POST /api/v1/data-quality-issues/{issue_id}/resolution -> Gửi ingestion_job_id + ghi chú để chờ xác minh
```

### 1.3. Cấu trúc lỗi chuẩn

Mọi lỗi nghiệp vụ (`AppException`) từ Backend trả về dạng:

```json
{
  "success": false,
  "error": {
    "code": "FORBIDDEN",
    "message": "Bạn không có quyền thực hiện thao tác này.",
    "details": {},
    "request_id": "req-123456"
  }
}
```

- `401 UNAUTHORIZED` / `INVALID_TOKEN`: Chưa gửi token hoặc token hết hạn/không hợp lệ -> FE gọi `/auth/refresh` hoặc điều hướng về trang đăng nhập.
- `403 FORBIDDEN` / `ROLE_REQUIRED`: Tài khoản không đủ quyền hoặc chưa có role hợp lệ.
- `422 Unprocessable Entity`: Lỗi validate schema Pydantic từ FastAPI (khi thiếu field bắt buộc, sai kiểu dữ liệu, dư field không cho phép) sẽ trả theo format mặc định của FastAPI `{ "detail": [...] }`.

---

## 2. Nhóm API Ngữ cảnh màn hình STAFF

### 2.1. Lấy thông tin quyền và cấu hình cho màn hình STAFF

- **Endpoint:** `GET /api/v1/staff/context`
- **Quyền:** `HR_STAFF`, `ACADEMIC_STAFF`, `ADMISSIONS_STAFF` (hoặc `ADMIN`, `DATA_ADMIN`)
- **Chức năng:** Gọi ngay sau khi mở giao diện STAFF để lấy danh sách lĩnh vực, phòng ban được phân công, các cờ quyền hiển thị nút chức năng và giới hạn file upload.

**Request:** Header `Authorization: Bearer <access_token>`. Không có query/body.

**Response `200 OK`:**

| Trường | Kiểu | Mô tả |
| --- | --- | --- |
| `user_id` | `integer` | ID tài khoản hiện tại |
| `username` | `string` | Tên đăng nhập |
| `roles` | `string[]` | Các role hợp lệ đang có |
| `domains` | `{ domain: string, name: string }[]` | Lĩnh vực được phép thao tác |
| `default_domain` | `string \| null` | Mã domain mặc định nếu user chỉ có 1 domain, ngược lại là `null` |
| `departments` | `{ department_id, department_code, department_name }[]` | Các phòng ban user đang được phân công hiệu lực |
| `can_upload` | `boolean` | Cho phép hiển thị tính năng upload |
| `can_resolve_issues` | `boolean` | `true` nếu có phân công phòng ban và có role STAFF |
| `can_manage_silver` | `boolean` | `false` với STAFF, `true` với Admin |
| `needs_department_assignment` | `boolean` | `true` nếu là STAFF nhưng chưa được phân công phòng ban -> UI cần hiển thị thông báo thay vì báo lỗi trống dữ liệu |
| `accepted_extensions` | `string[]` | Định dạng file hỗ trợ (`[".csv", ".xlsx", ".xlsm"]`) |
| `max_file_bytes` | `integer` | Dung lượng file tối đa (bytes) |
| `max_records` | `integer` | Số dòng tối đa cho phép |
| `note` | `string` | Ghi chú trạng thái phân công / hướng dẫn |

```json
{
  "user_id": 12,
  "username": "hr_staff_01",
  "roles": ["HR_STAFF"],
  "domains": [
    {
      "domain": "hr",
      "name": "Nhân sự"
    }
  ],
  "default_domain": "hr",
  "departments": [
    {
      "department_id": 3,
      "department_code": "HR",
      "department_name": "Phòng Tổ chức Nhân sự"
    }
  ],
  "can_upload": true,
  "can_resolve_issues": true,
  "can_manage_silver": false,
  "needs_department_assignment": false,
  "accepted_extensions": [".csv", ".xlsx", ".xlsm"],
  "max_file_bytes": 52428800,
  "max_records": 1000000,
  "note": "Chỉ người được giao mới được báo đã sửa"
}
```

**Lỗi thường gặp:** `401 UNAUTHORIZED`, `403 FORBIDDEN`, `503 STAFF_CONTEXT_UNAVAILABLE`.

---

## 3. Nhóm API Bộ dữ liệu Bronze (Datasets)

### 3.1. Danh sách bộ dữ liệu được phép nạp file

- **Endpoint:** `GET /api/v1/datasets`
- **Quyền:** `HR_STAFF`, `ACADEMIC_STAFF`, `ADMISSIONS_STAFF` (hoặc `ADMIN`, `DATA_ADMIN`)
- **Chức năng:** Lấy danh sách các bộ dữ liệu tầng `BRONZE` đang `ACTIVE` thuộc lĩnh vực của STAFF để user chọn khi upload file bổ sung.

**Query Parameters:**

| Tham số | Kiểu | Mặc định | Ràng buộc | Mô tả |
| --- | --- | --- | --- | --- |
| `limit` | `integer` | `20` | `1 <= limit <= 100` | Số lượng phần tử mỗi trang |
| `offset` | `integer` | `0` | `>= 0` | Vị trí bắt đầu |
| `search` | `string \| null` | `null` | `1..200` ký tự | Tìm kiếm không phân biệt hoa thường theo `dataset_name` |
| `domain` | `string \| null` | `null` | `"hr"` \| `"academic"` \| `"admissions"` | Lọc theo lĩnh vực |

**Response `200 OK` (`array`):**

```json
[
  {
    "dataset_id": 5,
    "dataset_code": "hr_employee_2026",
    "dataset_name": "Danh sách nhân sự 2026",
    "domain": "hr",
    "source_system_id": 1,
    "data_layer_id": 1,
    "owner_department_id": 3,
    "storage_format": "CSV",
    "location": "s3://bronze/hr/...",
    "version": "1",
    "status": "ACTIVE",
    "description": "Hồ sơ nhân sự định kỳ",
    "created_at": "2026-11-01T08:00:00Z",
    "updated_at": "2026-11-05T09:30:00Z"
  }
]
```

**Lỗi thường gặp:** `403 FORBIDDEN`.

---

### 3.2. Chi tiết một bộ dữ liệu được phép nạp file

- **Endpoint:** `GET /api/v1/datasets/{dataset_id}`
- **Quyền:** `HR_STAFF`, `ACADEMIC_STAFF`, `ADMISSIONS_STAFF` (hoặc `ADMIN`, `DATA_ADMIN`)
- **Chức năng:** Xem chi tiết metadata của một dataset `BRONZE` đang `ACTIVE` trong lĩnh vực được phép. Nếu `dataset_id` thuộc domain khác, API trả `404` để bảo mật.

**Path Parameters:**

| Tham số | Kiểu | Bắt buộc | Ràng buộc | Mô tả |
| --- | --- | --- | --- | --- |
| `dataset_id` | `integer` | Có | `> 0` | ID bộ dữ liệu |

**Response `200 OK`:** Trả về một object có cấu trúc giống một phần tử của `GET /api/v1/datasets`.

**Lỗi thường gặp:** `403 FORBIDDEN`, `404 DATASET_NOT_FOUND`.

---

## 4. Nhóm API Upload & Theo dõi Ingestion

### 4.1. Tải file lên để kiểm tra và lưu vào Bronze

- **Endpoint:** `POST /api/v1/ingestion/uploads`
- **Quyền:** `HR_STAFF`, `ACADEMIC_STAFF`, `ADMISSIONS_STAFF` (hoặc `ADMIN`, `DATA_ADMIN`)
- **Content-Type:** `multipart/form-data`
- **Chức năng:** Tiếp nhận file `.csv`, `.xlsx`, `.xlsm`, lưu tạm vào MinIO staging và tạo job `QUEUED` để worker kiểm tra dữ liệu và nạp vào tầng Bronze.

> **Quy tắc gửi form bắt buộc FE phải tuân thủ:**
> - **Trường hợp A — Nạp vào bộ dữ liệu đã có:** Chỉ gửi `dataset_id` (+ `file`, `parser_options`). **Không** gửi `dataset_name`, `description`, `domain`.
> - **Trường hợp B — Tạo bộ dữ liệu mới:** Bỏ trống `dataset_id`, gửi `dataset_name` (+ tùy chọn `description`). Nếu STAFF chỉ có 1 domain, không cần gửi `domain` (BE tự lấy từ quyền); nếu có nhiều domain cần gửi `domain`.
> - **Tên file (`file.filename`):** Chỉ chứa chữ cái không dấu, số, `_`, `.`, `-` (khớp regex `^[A-Za-z0-9_][A-Za-z0-9_.-]{0,199}$`).

**Form Fields (`multipart/form-data`):**

| Trường | Kiểu | Bắt buộc | Mặc định | Mô tả |
| --- | --- | --- | --- | --- |
| `file` | `File` | Có | - | File `.csv`, `.xlsx` hoặc `.xlsm` (không rỗng, tối đa `max_file_bytes`) |
| `dataset_id` | `integer` | Có điều kiện | `null` | ID bộ dữ liệu có sẵn (`> 0`). Không gửi khi tạo mới |
| `dataset_name` | `string` | Có điều kiện | `null` | Tên bộ dữ liệu mới (`1..200` ký tự). Không gửi khi đã chọn `dataset_id` |
| `description` | `string` | Không | `null` | Mô tả bộ dữ liệu mới (tối đa `4000` ký tự) |
| `domain` | `string` | Không | `null` | `"hr"` \| `"academic"` \| `"admissions"` (chỉ dùng khi tạo mới và tài khoản có nhiều domain) |
| `parser_options` | `string (JSON)` | Không | `"{}"` | Chuỗi JSON cấu hình đọc file |

**Chi tiết JSON cho `parser_options`:**
- Với CSV: `{"delimiter": ",", "encoding": "utf-8", "quote_char": "\"", "skip_rows": 0}`
- Với Excel (`.xlsx`, `.xlsm`): `{"sheet_name": "Sheet1", "header_row": 1, "skip_rows": 0}`

**Response `202 Accepted`:**

```json
{
  "ingestion_job_id": 124,
  "data_source_id": 8,
  "dataset_id": 5,
  "status": "QUEUED",
  "stage": "QUEUED",
  "job_type": "UPLOAD_V1",
  "source_table": "employees_nov.csv",
  "created_at": "2026-11-10T15:20:00Z",
  "started_at": null,
  "finished_at": null,
  "requested_by": "12",
  "rows_read": null,
  "rows_written": null,
  "rows_rejected": null,
  "error_message": null,
  "output": null,
  "validation_report": null,
  "root_job_id": 124,
  "attempt_number": 1,
  "quality_issue_id": null
}
```

**Các mã lỗi thường gặp:**
- `403 FORBIDDEN` / `UPLOAD_DOMAIN_FORBIDDEN`: Không có quyền upload hoặc chọn sai domain.
- `413 UPLOAD_TOO_LARGE`: File vượt quá `max_file_bytes`.
- `415 UPLOAD_FILE_TYPE_UNSUPPORTED`: Sai định dạng (không phải `.csv`, `.xlsx`, `.xlsm`).
- `422 UPLOAD_DESTINATION_INVALID`: Gửi đồng thời `dataset_id` và `dataset_name`, hoặc thiếu cả hai.
- `422 UPLOAD_DOMAIN_REQUIRED`: Cần chọn `domain` khi tạo mới.
- `422 UPLOAD_PARSER_INVALID`: JSON `parser_options` sai cú pháp hoặc không hợp lệ.
- `422 UPLOAD_FILENAME_INVALID`: Tên file chứa dấu tiếng Việt hoặc ký tự đặc biệt không hợp lệ.
- `422 UPLOAD_EMPTY`: File 0 byte.
- `503 INGESTION_QUEUE_UNAVAILABLE` / `UPLOAD_STORAGE_UNAVAILABLE`: Lỗi hệ thống hàng đợi/lưu trữ.

---

### 4.2. Danh sách các job ingestion

- **Endpoint:** `GET /api/v1/ingestion/jobs`
- **Quyền:** `HR_STAFF`, `ACADEMIC_STAFF`, `ADMISSIONS_STAFF` (hoặc `ADMIN`, `DATA_ADMIN`, `VIEWER` tùy scope)
- **Chức năng:** Lấy danh sách các lần nạp dữ liệu trong phạm vi lĩnh vực của user (hỗ trợ lọc & phân trang).

**Query Parameters:**

| Tham số | Kiểu | Mặc định | Ràng buộc | Mô tả |
| --- | --- | --- | --- | --- |
| `data_source_id` | `integer \| null` | `null` | `> 0` | Lọc theo nguồn dữ liệu |
| `dataset_id` | `integer \| null` | `null` | `> 0` | Lọc theo bộ dữ liệu |
| `status` | `string \| null` | `null` | `QUEUED`, `RETRY_WAIT`, `RUNNING`, `SUCCESS`, `FAILED`, `REJECTED` | Lọc theo trạng thái |
| `mine_only` | `boolean` | `false` | - | `true` để chỉ lấy job do chính user hiện tại tạo |
| `limit` | `integer` | `20` | `1..100` | Số dòng mỗi trang |
| `offset` | `integer` | `0` | `>= 0` | Vị trí bắt đầu |

**Response `200 OK` (`array`):** Danh sách các object có cấu trúc như response của `POST /api/v1/ingestion/uploads`.

---

### 4.3. Chi tiết một job ingestion

- **Endpoint:** `GET /api/v1/ingestion/jobs/{job_id}`
- **Quyền:** STAFF đúng domain
- **Chức năng:** Xem trạng thái chi tiết, số dòng đọc/ghi/từ chối, `validation_report` và `error_message` của một job. FE dùng API này để **polling mỗi 3–5 giây** sau khi upload cho đến khi `status` đạt `SUCCESS`, `FAILED` hoặc `REJECTED`.

**Path Parameters:**

| Tham số | Kiểu | Bắt buộc | Mô tả |
| --- | --- | --- | --- |
| `job_id` | `integer` | Có | `ingestion_job_id` |

**Response `200 OK`:** Trả về object `IngestionJobRead` (đầy đủ thông tin sau khi worker xử lý xong):

```json
{
  "ingestion_job_id": 124,
  "data_source_id": 8,
  "dataset_id": 5,
  "status": "SUCCESS",
  "stage": "COMMITTING",
  "job_type": "UPLOAD_V1",
  "source_table": "employees_nov.csv",
  "created_at": "2026-11-10T15:20:00Z",
  "started_at": "2026-11-10T15:20:02Z",
  "finished_at": "2026-11-10T15:20:05Z",
  "requested_by": "12",
  "rows_read": 250,
  "rows_written": 250,
  "rows_rejected": 0,
  "error_message": null,
  "output": {
    "domain": "hr",
    "records_read": 250,
    "source_type": "CSV"
  },
  "validation_report": {
    "can_accept": true,
    "checks": []
  },
  "root_job_id": 124,
  "attempt_number": 1,
  "quality_issue_id": null
}
```

---

### 4.4. Lịch sử các lần chạy thử của một job

- **Endpoint:** `GET /api/v1/ingestion/jobs/{job_id}/attempts`
- **Quyền:** STAFF đúng domain
- **Chức năng:** Xem chuỗi các lần chạy (attempt 1, 2, 3...) nếu job gặp lỗi tạm thời và được hệ thống tự động retry.

**Path Parameters:** `job_id` (`integer`).

**Response `200 OK` (`array`):**

```json
[
  {
    "ingestion_job_id": 124,
    "root_job_id": 124,
    "attempt_number": 1,
    "status": "FAILED",
    "stage": "WRITING",
    "error_message": "INGESTION_WRITING_TRANSIENT",
    "created_at": "2026-11-10T15:20:00Z",
    "started_at": "2026-11-10T15:20:02Z",
    "finished_at": "2026-11-10T15:20:04Z"
  },
  {
    "ingestion_job_id": 125,
    "root_job_id": 124,
    "attempt_number": 2,
    "status": "SUCCESS",
    "stage": "COMMITTING",
    "error_message": null,
    "created_at": "2026-11-10T15:20:14Z",
    "started_at": "2026-11-10T15:20:15Z",
    "finished_at": "2026-11-10T15:20:18Z"
  }
]
```

---

### 4.5. Thống kê số lượng job ingestion

- **Endpoint:** `GET /api/v1/ingestion/jobs/stats`
- **Quyền:** STAFF đúng domain
- **Chức năng:** Hiển thị thẻ thống kê tổng quan các job trong phạm vi domain của STAFF.

**Response `200 OK`:**

```json
{
  "total": 42,
  "queued": 1,
  "running": 0,
  "success": 38,
  "failed": 2,
  "retry_wait": 1
}
```

---

### 4.6. Chỉ số hiệu năng ingestion

- **Endpoint:** `GET /api/v1/ingestion/jobs/metrics`
- **Quyền:** STAFF đúng domain
- **Chức năng:** Lấy thời gian chờ trung bình, thời gian xử lý trung bình, tỷ lệ lỗi (%) và thời gian chờ lâu nhất của job đang `QUEUED`.

**Response `200 OK`:**

```json
{
  "average_wait_seconds": 1.85,
  "average_processing_seconds": 4.2,
  "failure_rate_percent": 4.76,
  "oldest_queued_seconds": 0.0
}
```

*(Các trường có thể là `null` nếu chưa có dữ liệu thống kê).*

---

## 5. Nhóm API Xử lý yêu cầu dữ liệu lỗi (Data Quality Issues)

> **Lưu ý cho UI:**
> - Các trạng thái hợp lệ (`status`): `ASSIGNED` (cần STAFF sửa) -> `PENDING_VERIFICATION` (đã nộp, chờ Admin xác minh) -> `VERIFIED` (đã đạt) hoặc trả lại `ASSIGNED` nếu Admin từ chối.
> - Nếu STAFF chưa được phân công phòng ban (`needs_department_assignment = true` ở `/staff/context`), không thể nhận hoặc xử lý issue.

### 5.1. Danh sách yêu cầu xử lý dữ liệu lỗi

- **Endpoint:** `GET /api/v1/data-quality-issues`
- **Quyền:** STAFF (hoặc MANAGER) có phân công phòng ban thuộc đúng domain
- **Chức năng:** Lấy danh sách các yêu cầu sửa dữ liệu lỗi trong phạm vi phòng ban & domain của tài khoản.

**Query Parameters:**

| Tham số | Kiểu | Mặc định | Ràng buộc | Mô tả |
| --- | --- | --- | --- | --- |
| `department_id` | `integer \| null` | `null` | `> 0` | Lọc theo phòng ban |
| `assignee_user_id` | `integer \| null` | `null` | `> 0` | Lọc theo ID người được giao |
| `status` | `string \| null` | `null` | `ASSIGNED`, `PENDING_VERIFICATION`, `VERIFIED` | Lọc theo trạng thái |
| `mine_only` | `boolean` | `false` | - | `true` để chỉ xem các yêu cầu được giao cho chính mình |
| `limit` | `integer` | `20` | `1..100` | Số dòng mỗi trang |
| `offset` | `integer` | `0` | `>= 0` | Vị trí bắt đầu |

**Response `200 OK` (`array`):**

```json
[
  {
    "issue_id": "a7f3c2d1-8b4e-4c9a-9f12-123456789abc",
    "dataset_id": 5,
    "department_id": 3,
    "domain": "hr",
    "title": "Thiếu họ tên nhân viên trong file tháng 11",
    "description": "Vui lòng bổ sung cột full_name ở các dòng lỗi và tải lại file.",
    "status": "ASSIGNED",
    "created_by": 1,
    "assignee_user_id": 12,
    "resolution_job_id": null,
    "verification_run_id": null,
    "created_at": "2026-11-10T09:00:00Z",
    "updated_at": "2026-11-10T09:00:00Z"
  }
]
```

---

### 5.2. Thống kê yêu cầu xử lý dữ liệu lỗi

- **Endpoint:** `GET /api/v1/data-quality-issues/stats`
- **Quyền:** STAFF (hoặc MANAGER) có phân công phòng ban
- **Chức năng:** Đếm số lượng yêu cầu theo từng trạng thái trong phạm vi được xem (nhận cùng bộ lọc với `GET /api/v1/data-quality-issues`, bỏ qua `limit`/`offset`).

**Query Parameters:** `department_id`, `assignee_user_id`, `status`, `mine_only` (tương tự `5.1`).

**Response `200 OK`:**

```json
{
  "total": 10,
  "assigned": 4,
  "pending_verification": 2,
  "verified": 4
}
```

---

### 5.3. Chi tiết yêu cầu xử lý dữ liệu lỗi

- **Endpoint:** `GET /api/v1/data-quality-issues/{issue_id}`
- **Quyền:** STAFF (hoặc MANAGER) thuộc đúng phòng ban & domain của yêu cầu
- **Chức năng:** Xem chi tiết yêu cầu, danh sách hồ sơ bị cách ly (`quarantine_records` gồm lý do lỗi, vị trí dòng `locator`, `source_table` là tên file bắt buộc phải giữ khi upload sửa) và lịch sử thay đổi trạng thái (`history`).

**Path Parameters:**

| Tham số | Kiểu | Bắt buộc | Mô tả |
| --- | --- | --- | --- |
| `issue_id` | `UUID` | Có | ID của yêu cầu xử lý |

**Response `200 OK`:**

```json
{
  "issue_id": "a7f3c2d1-8b4e-4c9a-9f12-123456789abc",
  "dataset_id": 5,
  "department_id": 3,
  "domain": "hr",
  "title": "Thiếu họ tên nhân viên trong file tháng 11",
  "description": "Vui lòng bổ sung cột full_name ở các dòng lỗi và tải lại file.",
  "status": "ASSIGNED",
  "created_by": 1,
  "assignee_user_id": 12,
  "resolution_job_id": null,
  "verification_run_id": null,
  "created_at": "2026-11-10T09:00:00Z",
  "updated_at": "2026-11-10T09:00:00Z",
  "quarantine_record_ids": [
    "11111111-2222-3333-4444-555555555555"
  ],
  "quarantine_records": [
    {
      "record_id": "11111111-2222-3333-4444-555555555555",
      "cleaning_run_id": "99999999-8888-7777-6666-555555555555",
      "ingestion_job_id": 120,
      "locator": "employees_nov.csv:row:15",
      "record_position": 14,
      "bronze_version": "v1",
      "error_codes": ["MISSING_REQUIRED_FIELD"],
      "issues": [
        {
          "field": "full_name",
          "code": "MISSING_REQUIRED_FIELD",
          "message": "Thiếu giá trị bắt buộc"
        }
      ],
      "entity": "employee",
      "source_table": "employees_nov.csv"
    }
  ],
  "history": [
    {
      "event_id": 1,
      "actor_id": 1,
      "event_type": "ASSIGNED",
      "from_status": null,
      "to_status": "ASSIGNED",
      "details": {
        "department_id": 3,
        "assignee_user_id": 12
      },
      "created_at": "2026-11-10T09:00:00Z"
    }
  ]
}
```

**Lỗi thường gặp:** `403 FORBIDDEN`, `404 QUALITY_ISSUE_NOT_FOUND`.

---

### 5.4. Tải lên file đã sửa cho yêu cầu

- **Endpoint:** `POST /api/v1/data-quality-issues/{issue_id}/uploads`
- **Quyền:** **Chỉ đúng `assignee_user_id`** đang có quyền và yêu cầu ở trạng thái `ASSIGNED`
- **Content-Type:** `multipart/form-data`
- **Chức năng:** Upload bản file đã sửa vào đúng `dataset_id` của yêu cầu. Backend tự gắn `quality_issue_id` vào job.

> **Lưu ý quan trọng cho UI:**
> - **Tên file upload phải khớp tuyệt đối** với `source_table` trong `quarantine_records` của issue (ví dụ `employees_nov.csv`).
> - Không cần gửi `dataset_id` hay `domain` (Backend tự lấy từ issue).
> - Bỏ trống `parser_options` để giữ nguyên cấu hình đọc file ban đầu (dấu phân cách CSV, encoding, sheet Excel...).
> - Sau khi nhận response `202`, FE lưu lại `ingestion_job_id` và **polling `GET /api/v1/ingestion/jobs/{job_id}`** đến khi `status == "SUCCESS"` rồi mới cho phép gọi API `5.5` (Báo đã sửa).

**Path Parameters:** `issue_id` (`UUID`).

**Form Fields (`multipart/form-data`):**

| Trường | Kiểu | Bắt buộc | Mặc định | Mô tả |
| --- | --- | --- | --- | --- |
| `file` | `File` | Có | - | Bản file đã sửa (giữ nguyên tên file nguồn) |
| `parser_options` | `string (JSON) \| null` | Không | `null` | Bỏ trống để dùng cấu hình đọc đã lưu của file lỗi |

**Response `202 Accepted`:** Cấu trúc `IngestionJobRead` (trong đó `quality_issue_id` sẽ mang giá trị của `issue_id`).

**Các mã lỗi thường gặp:**
- `403 FORBIDDEN`: Không phải người được giao phụ trách hoặc bị thu hồi quyền/phân công.
- `404 QUALITY_ISSUE_NOT_FOUND`: Không tìm thấy yêu cầu.
- `409 QUALITY_ISSUE_STATE_CONFLICT`: Yêu cầu không ở trạng thái `ASSIGNED`.
- `422 RESOLUTION_FILE_MISMATCH`: Tên file tải lên khác `source_table` của yêu cầu.
- `422 RESOLUTION_PARSER_REQUIRED` / `RESOLUTION_HEADER_REQUIRED`: Bản nguồn cũ chưa lưu `parser_options` hoặc không có dòng tiêu đề.

---

### 5.5. Báo đã sửa xong (Nộp kết quả sửa)

- **Endpoint:** `POST /api/v1/data-quality-issues/{issue_id}/resolution`
- **Quyền:** **Chỉ đúng `assignee_user_id`** khi yêu cầu đang ở trạng thái `ASSIGNED`
- **Chức năng:** Gửi `ingestion_job_id` của bản file sửa đã nạp Bronze `SUCCESS` và ghi chú xử lý. Nếu hợp lệ, trạng thái yêu cầu chuyển sang `PENDING_VERIFICATION` để Admin kiểm tra.

**Path Parameters:** `issue_id` (`UUID`).

**Request Body (`application/json`):**

| Trường | Kiểu | Bắt buộc | Ràng buộc | Mô tả |
| --- | --- | --- | --- | --- |
| `ingestion_job_id` | `integer` | Có | `> 0` | ID của job upload bản sửa đã đạt trạng thái `SUCCESS` |
| `note` | `string` | Có | `1..4000` ký tự | Ghi chú mô tả nội dung đã sửa |

```json
{
  "ingestion_job_id": 128,
  "note": "Đã bổ sung đầy đủ họ tên cho 5 dòng nhân viên bị thiếu."
}
```

**Response `200 OK`:** Trả về toàn bộ object `IssueDetail` cập nhật (giống `5.3`), với `"status": "PENDING_VERIFICATION"`, `"resolution_job_id": 128` và thêm sự kiện `"RESOLUTION_SUBMITTED"` trong `history`.

**Các mã lỗi thường gặp:**
- `403 FORBIDDEN`: Không phải người phụ trách.
- `409 QUALITY_ISSUE_STATE_CONFLICT`: Yêu cầu không còn ở trạng thái `ASSIGNED`.
- `422 INVALID_RESOLUTION_JOB`: Job chưa `SUCCESS`, sai dataset/tên file, hoặc dùng lại file cũ trước thời điểm tạo lỗi / lần nộp trước (khi bị Admin từ chối, STAFF bắt buộc phải upload một bản mới).
- `422 INVALID_RESOLUTION_OWNER`: Job upload không phải do chính người phụ trách tải lên hoặc thuộc yêu cầu khác.

---

## 6. API Hệ thống & Dùng chung khác

### 6.1. Kiểm tra kết nối hệ thống

- **Endpoint:** `GET /health`
- **Quyền:** Công khai
- **Response `200 OK`:**

```json
{
  "status": "ok"
}
```

### 6.2. Hỏi đáp dữ liệu bằng ngôn ngữ tự nhiên (NLQ)

- **Endpoint:** `POST /api/v1/nlq/ask`
- **Quyền:** Hiện tại router chưa gắn kiểm tra xác thực/role.
- **Chức năng:** Chuyển câu hỏi tiếng Việt/ngôn ngữ tự nhiên thành câu truy vấn SQL chỉ đọc và trả kết quả.

**Request Body (`application/json`):**

```json
{
  "question": "Thống kê số lượng nhân viên theo phòng ban"
}
```

**Response `200 OK`:**

```json
{
  "sql": "SELECT department_name, COUNT(*) AS total FROM ...",
  "rows": [
    {
      "department_name": "Phòng Tổ chức Nhân sự",
      "total": 45
    }
  ]
}
```
