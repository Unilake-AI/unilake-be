# Tài liệu Đặc tả Nghiệp vụ & Chi tiết API Phân hệ STAFF (UniLake AI)

Tài liệu này tổng hợp toàn bộ các vai trò **STAFF** theo từng phòng ban, quy trình xử lý nghiệp vụ, ranh giới phân quyền, và chi tiết đặc tả kỹ thuật từng API (kèm đầy đủ cấu trúc **Request**, **Response** và **Mã lỗi**) trên hệ thống UniLake AI.

- **Môi trường / Base URL:** `http://localhost:8000/api/v1`
- **Xác thực:** Bearer Token trong Header `Authorization: Bearer <access_token>`
- **Tracing Header:** `X-Request-ID` (Backend tự sinh trong mỗi request để truy vết sự cố)

---

## 1. Phân định Vai trò STAFF theo từng Phòng ban

| Role Code | Tên hiển thị | Miền nghiệp vụ (`domain`) | Phòng ban phụ trách | Phạm vi dữ liệu quản lý |
| :--- | :--- | :---: | :--- | :--- |
| **`HR_STAFF`** | Nhân viên Nhân sự | `hr` | Phòng Tổ chức Nhân sự (`PNS`) | Hồ sơ cán bộ, giảng viên, hợp đồng lao động, quá trình công tác. |
| **`ACADEMIC_STAFF`** | Nhân viên Đào tạo | `academic` | Phòng Quản lý Đào tạo (`PDT`) | Danh sách sinh viên, lớp học phần, điểm thi, môn học, chương trình đào tạo. |
| **`ADMISSIONS_STAFF`** | Nhân viên Tuyển sinh | `admissions` | Phòng Tuyển sinh & Truyền thông (`PTS`) | Hồ sơ đăng ký tuyển sinh, kết quả xét tuyển, danh sách nhập học. |

> **Nguyên tắc cô lập nghiệp vụ (Scope Isolation):**  
> Nhân viên thuộc phòng ban nào chỉ được xem, nạp file và xử lý lỗi cho các bộ dữ liệu thuộc đúng Domain và Phòng ban đó. Truy cập sang domain khác sẽ nhận `403 FORBIDDEN` hoặc `404 NOT FOUND`.

---

## 2. Sơ đồ Luồng Nghiệp vụ Cốt lõi của STAFF

```text
========================================================================================
LUỒNG 1: KHỞI TẠO NGỮ CẢNH (Sau khi đăng nhập)
========================================================================================
  [Client FE] ──► GET /api/v1/staff/context ──► Nhận domain, phòng ban, hạn mức file, quyền

========================================================================================
LUỒNG 2: NẠP DỮ LIỆU ĐỊNH KỲ VÀO TẦNG BRONZE
========================================================================================
  1. [Client FE] ──► GET /api/v1/datasets ──────► Chọn bộ dữ liệu có sẵn của phòng mình
  2. [Client FE] ──► POST /api/v1/ingestion/uploads (gửi file CSV/Excel + dataset_id)
                     └──► Nhận HTTP 202 Accepted, trả về ingestion_job_id (status: QUEUED)
  3. [Client FE] ──► GET /api/v1/ingestion/jobs/{id} (Polling mỗi 3-5s)
                     └──► Chờ đến khi status: SUCCESS (Đã lưu Bronze) hoặc FAILED

========================================================================================
LUỒNG 3: XỬ LÝ DỮ LIỆU LỖI & KHẮC PHỤC HỒ SƠ CÁCH LY (Remediation Workflow)
========================================================================================
  1. [Client FE] ──► GET /api/v1/data-quality-issues?mine_only=true&status=ASSIGNED
  2. [Client FE] ──► GET /api/v1/data-quality-issues/{id}
                     └──► Xem chi tiết: dòng lỗi (locator), cột lỗi, tên file gốc (source_table)
  3. [Client FE] ──► Chỉnh sửa file gốc ngoài Excel/CSV
  4. [Client FE] ──► POST /api/v1/data-quality-issues/{id}/uploads (Gửi file sửa - TRÙNG TÊN NGUỒN)
                     └──► Nhận HTTP 202, trả về job_id mới
  5. [Client FE] ──► Polling GET /api/v1/ingestion/jobs/{job_id} cho đến khi SUCCESS
  6. [Client FE] ──► POST /api/v1/data-quality-issues/{id}/resolution
                     └──► Gửi {"ingestion_job_id": job_id, "note": "Đã sửa..."}
                     └──► Status chuyển sang PENDING_VERIFICATION (chờ Data Admin duyệt)
```

---

## 3. Đặc tả Chi tiết từng API (Request / Response / Error)

---

### NHÓM 1: NGỮ CẢNH LÀM VIỆC (CONTEXT)

#### 1. Lấy thông tin quyền và cấu hình màn hình STAFF
* **Endpoint:** `GET /api/v1/staff/context`
* **Quyền hạn:** `HR_STAFF`, `ACADEMIC_STAFF`, `ADMISSIONS_STAFF`
* **Mục đích:** Khởi tạo giao diện, kiểm tra phòng ban được phân công, lấy danh mục định dạng file và dung lượng tối đa.

**Request Header:**
```http
GET /api/v1/staff/context HTTP/1.1
Host: localhost:8000
Authorization: Bearer <access_token>
```

**Response `200 OK`:**
```json
{
  "user_id": 12,
  "username": "hr_staff",
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
      "department_id": 1,
      "department_code": "PNS",
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

**Mã lỗi thường gặp:**
* `401 UNAUTHORIZED`: Chưa đăng nhập hoặc token hết hạn.
* `403 FORBIDDEN`: Không có vai trò STAFF hợp lệ.
* `503 STAFF_CONTEXT_UNAVAILABLE`: Sự cố kết nối database.

---

### NHÓM 2: BỘ DỮ LIỆU BRONZE (DATASETS)

#### 2. Danh sách bộ dữ liệu được phép nạp file
* **Endpoint:** `GET /api/v1/datasets`
* **Quyền hạn:** STAFF thuộc đúng domain
* **Mục đích:** Lấy danh mục các bộ dữ liệu tầng Bronze của phòng mình để hiển thị lên dropdown nạp file.

**Query Parameters:**
| Tham số | Kiểu | Bắt buộc | Mặc định | Mô tả |
| :--- | :---: | :---: | :---: | :--- |
| `limit` | integer | Không | `20` | Giới hạn số lượng (1 - 100) |
| `offset` | integer | Không | `0` | Vị trí bắt đầu |
| `search` | string | Không | null | Tìm kiếm theo tên bộ dữ liệu |
| `domain` | string | Không | null | Lọc theo `hr`, `academic`, `admissions` |

**Request:**
```http
GET /api/v1/datasets?domain=hr&limit=20&offset=0 HTTP/1.1
Authorization: Bearer <access_token>
```

**Response `200 OK`:**
```json
[
  {
    "dataset_id": 5,
    "dataset_code": "hr_employees_raw",
    "dataset_name": "Hồ sơ Cán bộ - Giảng viên 2026",
    "domain": "hr",
    "source_system_id": 1,
    "data_layer_id": 1,
    "owner_department_id": 1,
    "storage_format": "CSV",
    "location": "s3://bronze/hr/employees/...",
    "version": "1",
    "status": "ACTIVE",
    "description": "Dữ liệu định kỳ do phòng Nhân sự nạp",
    "created_at": "2026-11-01T08:00:00Z",
    "updated_at": "2026-11-15T09:30:00Z"
  }
]
```

---

#### 3. Chi tiết một bộ dữ liệu
* **Endpoint:** `GET /api/v1/datasets/{dataset_id}`
* **Quyền hạn:** STAFF thuộc đúng domain của dataset

**Request:**
```http
GET /api/v1/datasets/5 HTTP/1.1
Authorization: Bearer <access_token>
```

**Response `200 OK`:** Trả về đối tượng JSON chi tiết bộ dữ liệu (như một phần tử trong danh sách trên).  
**Mã lỗi:** `404 DATASET_NOT_FOUND` (Nếu ID không tồn tại hoặc thuộc phòng ban khác).

---

### NHÓM 3: NẠP DỮ LIỆU & GIÁM SÁT TIẾN TRÌNH (INGESTION)

#### 4. Tải file lên kho Bronze
* **Endpoint:** `POST /api/v1/ingestion/uploads`
* **Quyền hạn:** STAFF thuộc đúng domain
* **Content-Type:** `multipart/form-data`
* **Mục đích:** Đưa file thô vào MinIO Staging và xếp hàng chờ Worker xử lý lưu Bronze.

**Form Data Fields:**
| Trường | Kiểu | Bắt buộc | Mô tả |
| :--- | :---: | :---: | :--- |
| `file` | Binary File | **Có** | File `.csv`, `.xlsx` hoặc `.xlsm` (dung lượng `<= 50MB`) |
| `dataset_id` | integer | **Có** | ID bộ dữ liệu đích (thuộc phòng ban mình) |
| `parser_options` | string (JSON) | Không | Tuỳ chọn đọc file (ví dụ sheet Excel, ký tự ngăn cách CSV) |

*Ví dụ `parser_options`:*
* Cho CSV: `'{"delimiter": ",", "encoding": "utf-8", "quote_char": "\"", "skip_rows": 0}'`
* Cho Excel: `'{"sheet_name": "NhanSu", "header_row": 1, "skip_rows": 0}'`

**Response `202 Accepted`:**
```json
{
  "ingestion_job_id": 105,
  "data_source_id": 8,
  "dataset_id": 5,
  "status": "QUEUED",
  "stage": "QUEUED",
  "job_type": "UPLOAD_V1",
  "source_table": "hr_employees_q4.csv",
  "created_at": "2026-11-18T14:20:00Z",
  "started_at": null,
  "finished_at": null,
  "requested_by": "12",
  "rows_read": null,
  "rows_written": null,
  "rows_rejected": null,
  "error_message": null,
  "output": null,
  "validation_report": null,
  "root_job_id": 105,
  "attempt_number": 1,
  "quality_issue_id": null
}
```

**Mã lỗi thường gặp:**
* `413 UPLOAD_TOO_LARGE`: Dung lượng file vượt quá 50MB.
* `415 UPLOAD_FILE_TYPE_UNSUPPORTED`: Không phải file `.csv`, `.xlsx`, `.xlsm`.
* `422 UPLOAD_FILENAME_INVALID`: Tên file chứa dấu tiếng Việt, khoảng trắng hoặc ký tự đặc biệt.
* `422 UPLOAD_EMPTY`: File rỗng 0 byte.
* `422 UPLOAD_PARSER_INVALID`: Chuỗi JSON `parser_options` sai định dạng.

---

#### 5. Danh sách các lần nạp dữ liệu (Ingestion Jobs)
* **Endpoint:** `GET /api/v1/ingestion/jobs`
* **Quyền hạn:** STAFF thuộc đúng domain

**Query Parameters:**
| Tham số | Kiểu | Mô tả |
| :--- | :---: | :--- |
| `status` | string | `QUEUED`, `RUNNING`, `SUCCESS`, `FAILED`, `REJECTED` |
| `dataset_id` | integer | Lọc theo bộ dữ liệu |
| `mine_only` | boolean | `true`: chỉ lấy các lần do chính mình tải |
| `limit` / `offset` | integer | Phân trang |

**Response `200 OK`:** Trả về danh sách mảng các Job nạp dữ liệu (`IngestionJobRead[]`).

---

#### 6. Theo dõi chi tiết 1 lần nạp (Polling trạng thái)
* **Endpoint:** `GET /api/v1/ingestion/jobs/{job_id}`
* **Quyền hạn:** STAFF thuộc đúng domain
* **Mục đích:** FE gọi định kỳ 3–5 giây sau khi upload để kiểm tra kết quả nạp vào Bronze.

**Response `200 OK` (Khi Job đã hoàn tất thành công):**
```json
{
  "ingestion_job_id": 105,
  "data_source_id": 8,
  "dataset_id": 5,
  "status": "SUCCESS",
  "stage": "COMMITTING",
  "job_type": "UPLOAD_V1",
  "source_table": "hr_employees_q4.csv",
  "created_at": "2026-11-18T14:20:00Z",
  "started_at": "2026-11-18T14:20:02Z",
  "finished_at": "2026-11-18T14:20:05Z",
  "requested_by": "12",
  "rows_read": 350,
  "rows_written": 350,
  "rows_rejected": 0,
  "error_message": null,
  "output": {
    "domain": "hr",
    "records_read": 350,
    "source_type": "CSV"
  },
  "validation_report": {
    "can_accept": true,
    "result": "PASS",
    "records_scanned": 350,
    "total_errors": 0,
    "total_warnings": 0
  },
  "root_job_id": 105,
  "attempt_number": 1,
  "quality_issue_id": null
}
```

---

#### 7. Thống kê số lượng Job nạp liệu
* **Endpoint:** `GET /api/v1/ingestion/jobs/stats`
* **Quyền hạn:** STAFF thuộc đúng domain

**Response `200 OK`:**
```json
{
  "total": 52,
  "queued": 0,
  "running": 1,
  "success": 48,
  "failed": 3,
  "retry_wait": 0
}
```

---

#### 8. Chỉ số hiệu năng nạp liệu
* **Endpoint:** `GET /api/v1/ingestion/jobs/metrics`
* **Quyền hạn:** STAFF thuộc đúng domain

**Response `200 OK`:**
```json
{
  "average_wait_seconds": 1.25,
  "average_processing_seconds": 3.8,
  "failure_rate_percent": 5.76,
  "oldest_queued_seconds": 0.0
}
```

---

### NHÓM 4: XỬ LÝ DỮ LIỆU LỖI (DATA QUALITY REMEDIATION)

#### 9. Danh sách yêu cầu xử lý dữ liệu lỗi
* **Endpoint:** `GET /api/v1/data-quality-issues`
* **Quyền hạn:** STAFF thuộc đúng phòng ban & domain

**Query Parameters:**
| Tham số | Kiểu | Mô tả |
| :--- | :---: | :--- |
| `status` | string | `ASSIGNED` (cần sửa), `PENDING_VERIFICATION` (chờ duyệt), `VERIFIED` (đã xong) |
| `mine_only` | boolean | `true`: chỉ lấy các yêu cầu giao cho chính mình phụ trách |
| `limit` / `offset` | integer | Phân trang |

**Response `200 OK`:**
```json
[
  {
    "issue_id": "7b8e1a3c-5d2f-4e9b-8c11-9876543210ab",
    "dataset_id": 5,
    "department_id": 1,
    "domain": "hr",
    "title": "Sai định dạng ngày sinh và thiếu mã nhân viên",
    "description": "Vui lòng đối soát hồ sơ gốc, bổ sung employee_code và chuẩn hóa date_of_birth.",
    "status": "ASSIGNED",
    "created_by": 2,
    "assignee_user_id": 12,
    "resolution_job_id": null,
    "verification_run_id": null,
    "created_at": "2026-11-18T10:00:00Z",
    "updated_at": "2026-11-18T10:00:00Z"
  }
]
```

---

#### 10. Thống kê số lượng yêu cầu xử lý lỗi
* **Endpoint:** `GET /api/v1/data-quality-issues/stats`
* **Quyền hạn:** STAFF thuộc đúng phòng ban & domain

**Response `200 OK`:**
```json
{
  "total": 8,
  "assigned": 3,
  "pending_verification": 2,
  "verified": 3
}
```

---

#### 11. Xem chi tiết yêu cầu xử lý lỗi (Quan trọng để sửa file)
* **Endpoint:** `GET /api/v1/data-quality-issues/{issue_id}`
* **Quyền hạn:** STAFF cùng phòng ban có quyền xem (người khác phòng trả `404`)

**Response `200 OK`:**
```json
{
  "issue_id": "7b8e1a3c-5d2f-4e9b-8c11-9876543210ab",
  "dataset_id": 5,
  "department_id": 1,
  "domain": "hr",
  "title": "Sai định dạng ngày sinh và thiếu mã nhân viên",
  "description": "Vui lòng đối soát hồ sơ gốc, bổ sung employee_code và chuẩn hóa date_of_birth.",
  "status": "ASSIGNED",
  "created_by": 2,
  "assignee_user_id": 12,
  "resolution_job_id": null,
  "verification_run_id": null,
  "created_at": "2026-11-18T10:00:00Z",
  "updated_at": "2026-11-18T10:00:00Z",
  "quarantine_record_ids": [
    "aa11bb22-cc33-dd44-ee55-ff6677889900"
  ],
  "quarantine_records": [
    {
      "record_id": "aa11bb22-cc33-dd44-ee55-ff6677889900",
      "cleaning_run_id": "11223344-5566-7788-99aa-bbccddeeff00",
      "ingestion_job_id": 105,
      "locator": "hr_employees_q4.csv:row:18",
      "record_position": 17,
      "bronze_version": "v1",
      "error_codes": ["REQUIRED_VALUE_MISSING", "FORMAT_DATE_INVALID"],
      "issues": [
        {
          "field": "employee_code",
          "code": "REQUIRED_VALUE_MISSING",
          "message": "Trường bắt buộc không được để trống"
        },
        {
          "field": "date_of_birth",
          "code": "FORMAT_DATE_INVALID",
          "message": "Định dạng ngày sinh không hợp lệ"
        }
      ],
      "entity": "employee",
      "source_table": "hr_employees_q4.csv"
    }
  ],
  "history": [
    {
      "event_id": 1,
      "actor_id": 2,
      "event_type": "ASSIGNED",
      "from_status": null,
      "to_status": "ASSIGNED",
      "details": {
        "department_id": 1,
        "assignee_user_id": 12
      },
      "created_at": "2026-11-18T10:00:00Z"
    }
  ]
}
```

---

#### 12. Tải lên file đã sửa lỗi
* **Endpoint:** `POST /api/v1/data-quality-issues/{issue_id}/uploads`
* **Quyền hạn:** **Chỉ đúng `assignee_user_id`** (người được giao) khi Issue đang ở trạng thái `ASSIGNED`
* **Content-Type:** `multipart/form-data`

**Ràng buộc bắt buộc:**
1. **Tên file tải lên phải trùng khớp 100% với `source_table`** của Issue (trong ví dụ trên bắt buộc phải đặt tên là `hr_employees_q4.csv`).
2. Không cần gửi `dataset_id` hay `domain` (Backend tự lấy từ Issue).
3. Bỏ trống `parser_options` để tự động kế thừa cấu hình đọc ban đầu.

**Form Data:**
* `file`: File đã sửa hoàn chỉnh trên máy tính.

**Response `202 Accepted`:** Trả về đối tượng `IngestionJobRead` với trường `quality_issue_id` mang giá trị của `issue_id`.  
*(Sau bước này, Staff polling `GET /ingestion/jobs/{job_id}` chờ trạng thái `SUCCESS` rồi mới thực hiện API số 13 bên dưới).*

**Mã lỗi thường gặp:**
* `403 FORBIDDEN`: Đồng nghiệp cùng phòng hoặc người khác nộp thay người phụ trách.
* `409 QUALITY_ISSUE_STATE_CONFLICT`: Issue không ở trạng thái `ASSIGNED` (ví dụ đang chờ duyệt).
* `422 RESOLUTION_FILE_MISMATCH`: Tên file tải lên bị lệch so với `source_table`.

---

#### 13. Báo cáo hoàn thành (Nộp kết quả sửa)
* **Endpoint:** `POST /api/v1/data-quality-issues/{issue_id}/resolution`
* **Quyền hạn:** **Chỉ đúng `assignee_user_id`**
* **Content-Type:** `application/json`
* **Mục đích:** Gửi bằng chứng đã sửa (`ingestion_job_id`) để chuyển giao cho Data Admin kiểm tra và xuất bản lên Silver.

**Request Body:**
```json
{
  "ingestion_job_id": 108,
  "note": "Đã bổ sung mã nhân viên NV0045 và chuẩn hóa ngày sinh về định dạng YYYY-MM-DD cho dòng 18."
}
```

**Response `200 OK`:**
```json
{
  "issue_id": "7b8e1a3c-5d2f-4e9b-8c11-9876543210ab",
  "dataset_id": 5,
  "department_id": 1,
  "domain": "hr",
  "status": "PENDING_VERIFICATION",
  "resolution_job_id": 108,
  "updated_at": "2026-11-18T15:30:00Z"
}
```

**Mã lỗi thường gặp:**
* `422 INVALID_RESOLUTION_JOB`:
  * Job nạp chưa đạt trạng thái `SUCCESS` (vẫn đang `RUNNING` hoặc bị `FAILED`).
  * Job nạp không phải của tệp thuộc Issue này.
  * Tái sử dụng lại mã `ingestion_job_id` cũ đã từng bị Admin từ chối trước đó (khi bị từ chối, bắt buộc phải upload tạo job mới).
* `422 INVALID_RESOLUTION_OWNER`: Job nạp do người khác tải lên, không phải người phụ trách.

---

### NHÓM 5: CHUÔNG THÔNG BÁO & TIỆN ÍCH

#### 14. Danh sách thông báo của nhân viên
* **Endpoint:** `GET /api/v1/notifications?limit=20&offset=0&unread_only=false`
* **Quyền hạn:** Mọi tài khoản STAFF đã đăng nhập

**Response `200 OK`:**
```json
{
  "items": [
    {
      "notification_id": 101,
      "title": "Bạn được phân công xử lý dữ liệu lỗi mới",
      "message": "Yêu cầu 'Sai định dạng ngày sinh...' thuộc bộ dữ liệu Hồ sơ Cán bộ đã được giao cho bạn.",
      "link": "/staff/issues/7b8e1a3c-5d2f-4e9b-8c11-9876543210ab",
      "is_read": false,
      "created_at": "2026-11-18T10:00:05Z"
    }
  ],
  "total": 12,
  "unread_count": 3
}
```

#### 15. Đánh dấu thông báo đã đọc
* **Endpoint:** `POST /api/v1/notifications/{notification_id}/read`
* **Response `200 OK`:** `{"success": true}`

#### 16. Hỏi đáp dữ liệu thông minh
* **Endpoint:** `POST /api/v1/nlq/ask`
* **Request:** `{"question": "Thống kê tổng số nhân sự đang công tác hiện tại"}`
* **Response `200 OK`:** Trả về câu trả lời tự nhiên + bảng số liệu truy vấn.

---

## 4. Bảng Tra cứu Mã Lỗi Nghiệp vụ Phổ biến (Error Dictionary)

| HTTP Status | Mã lỗi Backend (`error.code`) | Ý nghĩa & Nguyên nhân | Hướng xử lý cho Staff |
| :---: | :--- | :--- | :--- |
| `401` | `UNAUTHORIZED` / `INVALID_TOKEN` | Token hết hạn hoặc chưa đăng nhập. | Đăng nhập lại để nhận token mới. |
| `403` | `FORBIDDEN` | Gọi API ngoài quyền hạn hoặc sang phòng ban khác. | Kiểm tra lại phân công phòng ban. |
| `403` | `UPLOAD_DOMAIN_FORBIDDEN` | Nhân viên HR upload vào dataset Đào tạo hoặc ngược lại. | Chọn đúng dataset của phòng mình. |
| `404` | `DATASET_NOT_FOUND` | Dataset không tồn tại hoặc thuộc domain khác. | Kiểm tra lại danh sách dataset khả dụng. |
| `404` | `QUALITY_ISSUE_NOT_FOUND` | Issue không tồn tại hoặc thuộc phòng ban khác. | Kiểm tra lại đường dẫn Issue. |
| `409` | `QUALITY_ISSUE_STATE_CONFLICT` | Cố tình nộp sửa khi Issue đang `PENDING_VERIFICATION` hoặc `VERIFIED`. | Chờ Admin duyệt hoặc yêu cầu mở lại. |
| `413` | `UPLOAD_TOO_LARGE` | File vượt quá hạn mức `max_file_bytes` (50MB). | Giảm bớt dung lượng file hoặc chia nhỏ. |
| `415` | `UPLOAD_FILE_TYPE_UNSUPPORTED` | Upload file `.pdf`, `.txt`, `.json`, `.xls` cũ. | Chuyển đổi file về `.csv`, `.xlsx`, `.xlsm`. |
| `422` | `UPLOAD_FILENAME_INVALID` | Tên file chứa dấu tiếng Việt hoặc khoảng trắng. | Đổi tên file dạng `tieng_viet_khong_dau.xlsx`. |
| `422` | `UPLOAD_EMPTY` | File rỗng 0 byte. | Kiểm tra nội dung file trước khi tải. |
| `422` | `RESOLUTION_FILE_MISMATCH` | Tải file sửa có tên khác với tên file nguồn gốc. | Đổi tên file sửa trùng khớp 100% với `source_table`. |
| `422` | `INVALID_RESOLUTION_JOB` | Nộp Job chưa SUCCESS hoặc nộp lại Job cũ đã bị từ chối. | Tải file mới và chờ Job đạt SUCCESS. |

---

## 5. Tài khoản Kiểm thử Mẫu (Seed Data)

* Mật khẩu mặc định cho toàn bộ tài khoản thử nghiệm: **`Password@123`**

| Tài khoản | Vai trò (`role`) | Phòng ban | Mục đích kiểm thử |
| :--- | :--- | :--- | :--- |
| `hr_staff` | `HR_STAFF` | Phòng Tổ chức Nhân sự (`PNS`) | Kiểm thử toàn bộ luồng nạp và sửa lỗi Nhân sự. |
| `academic_staff` | `ACADEMIC_STAFF` | Phòng Quản lý Đào tạo (`PDT`) | Kiểm thử luồng Đào tạo & chặn chéo Nhân sự. |
| `admissions_staff` | `ADMISSIONS_STAFF` | Phòng Tuyển sinh (`PTS`) | Kiểm thử luồng Tuyển sinh. |
