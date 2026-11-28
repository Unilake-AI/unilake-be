# Tài liệu Đặc tả Nghiệp vụ & Chi tiết API Phân hệ MANAGER (UniLake AI)

Tài liệu này tổng hợp toàn bộ các vai trò **MANAGER (Trưởng phòng / Quản lý nghiệp vụ)** theo từng phòng ban, quy trình nghiệp vụ, ranh giới phân quyền và chi tiết đặc tả kỹ thuật từng API (kèm đầy đủ cấu trúc **Request**, **Response** và **Mã lỗi**) trên hệ thống UniLake AI.

- **Môi trường / Base URL:** `http://localhost:8000/api/v1`
- **Xác thực:** Bearer Token trong Header `Authorization: Bearer <access_token>`
- **Tracing Header:** `X-Request-ID` (Backend tự sinh trong mỗi request để truy vết sự cố)

---

## 1. Phân định Vai trò MANAGER theo từng Phòng ban

| Role Code | Tên hiển thị | Miền nghiệp vụ (`domain`) | Phòng ban phụ trách | Phạm vi dữ liệu quản lý |
| :--- | :--- | :---: | :--- | :--- |
| **`HR_MANAGER`** | Trưởng phòng Nhân sự | `hr` | Phòng Tổ chức Nhân sự (`PNS`) | Báo cáo Gold nhân sự, KPI cơ cấu cán bộ/giảng viên, duyệt quy tắc chuẩn hóa nhân sự, giám sát lỗi dữ liệu PNS. |
| **`ACADEMIC_MANAGER`** | Trưởng phòng Đào tạo | `academic` | Phòng Quản lý Đào tạo (`PDT`) | Báo cáo Gold sinh viên/học phần/điểm thi, duyệt quy tắc đào tạo, giám sát lỗi dữ liệu PDT. |
| **`ADMISSIONS_MANAGER`** | Trưởng phòng Tuyển sinh | `admissions` | Phòng Tuyển sinh & TT (`PTS`) | Báo cáo Gold hồ sơ trúng tuyển/nhập học, duyệt quy tắc tuyển sinh, giám sát lỗi dữ liệu PTS. |

### Ranh giới Quyền hạn của Manager:
* **Những việc Manager LÀM:**
  * Giám sát hiện trạng và chỉ số KPI tầng Gold của phòng ban mình.
  * Xem danh mục Dataset và sơ đồ truy vết nguồn gốc (Data Lineage).
  * Quy hoạch, đăng ký sẵn Bộ dữ liệu chuẩn mực để nhân viên phòng mình nạp file.
  * Giám sát tiến độ xử lý dữ liệu lỗi của nhân viên (`STAFF`).
  * Thực hiện vai trò **Domain Owner**: Phê duyệt hoặc thu hồi các quy tắc làm sạch dữ liệu do Data Admin cấu hình.
  * Hỏi đáp dữ liệu với trợ lý ảo UniLake AI.
* **Những việc Manager KHÔNG LÀM:**
  * ❌ **Không upload file thô:** Đây là nhiệm vụ của `STAFF`.
  * ❌ **Không trực tiếp nộp file sửa lỗi:** Đây là nhiệm vụ của `STAFF`.
  * ❌ **Không điều khiển Worker nạp liệu hay chạy Cleaning Engine:** Đây là trách nhiệm kỹ thuật của `DATA_ADMIN`.
  * ❌ **Không xem chéo dữ liệu phòng ban khác:** Không xem dữ liệu của domain khác (trừ `ADMIN`, `DATA_ADMIN` và `VIEWER`).

---

## 2. Sơ đồ Luồng Nghiệp vụ Cốt lõi của MANAGER

```text
========================================================================================
LUỒNG 1: KHỞI TẠO NGỮ CẢNH QUẢN LÝ
========================================================================================
  [Client FE] ──► GET /api/v1/manager/context ──► Lấy domain, phòng ban sở hữu, cờ cấu hình

========================================================================================
LUỒNG 2: GIÁM SÁT TỔNG QUAN & PHÂN TÍCH KPI NGHIỆP VỤ (GOLD ANALYTICS)
========================================================================================
  1. [Client FE] ──► GET /api/v1/manager/overview ─────────► Xem mức sẵn sàng dữ liệu Gold,
                                                            thời điểm cập nhật, KPI tóm tắt
  2. [Client FE] ──► GET /api/v1/manager/analytics ────────► Xem biểu đồ phân tích chuyên sâu
  3. [Client FE] ──► GET /api/v1/manager/analysis-options ─► Lấy danh sách thực thể & chỉ số lọc

========================================================================================
LUỒNG 3: QUẢN LÝ TÀI SẢN DỮ LIỆU & NGUỒN GỐC (DATA CATALOG & LINEAGE)
========================================================================================
  1. [Client FE] ──► GET /api/v1/manager/catalog ──────────► Xem danh sách dataset của phòng
  2. [Client FE] ──► GET /api/v1/manager/catalog/{id} ─────► Xem chi tiết cấu trúc cột & bảng
  3. [Client FE] ──► GET /api/v1/manager/catalog/{id}/lineage ► Xem đồ thị nguồn gốc dữ liệu
  4. [Client FE] ──► POST /api/v1/datasets ────────────────► Tạo bộ dữ liệu chuẩn mực mới

========================================================================================
LUỒNG 4: GIÁM SÁT CHẤT LƯỢNG DỮ LIỆU & TIẾN ĐỘ SỬA LỖI CỦA NHÂN VIÊN
========================================================================================
  1. [Client FE] ──► GET /api/v1/manager/issues?status=... ──► Danh sách lỗi của phòng mình
  2. [Client FE] ──► GET /api/v1/manager/issues/stats ───────► Thống kê số lượng theo trạng thái
  3. [Client FE] ──► GET /api/v1/manager/issues/{issue_id} ──► Chi tiết lỗi & nhân viên phụ trách

========================================================================================
LUỒNG 5: PHÊ DUYỆT QUY TẮC CHUẨN HÓA DỮ LIỆU (PROFILE GOVERNANCE / REVIEW QUEUE)
========================================================================================
  1. [Client FE] ──► GET /api/v1/pipeline/profiles/review-queue ──► Hàng đợi quy tắc chờ duyệt
  2. [Client FE] ──► GET /api/v1/pipeline/profiles/{id}/revisions ─► Lịch sử phiên bản quy tắc
  3. [Client FE] ──► POST /api/v1/pipeline/profiles/{id}/approve ──► Bấm duyệt quy tắc mới
  4. [Client FE] ──► POST /api/v1/pipeline/profiles/{id}/revisions/{rev}/revoke ──► Thu hồi duyệt

========================================================================================
LUỒNG 6: TƯƠNG TÁC THÔNG MINH & TRA CỨU LỊCH SỬ (UNILAKE AI & HISTORY)
========================================================================================
  1. [Client FE] ──► POST /api/v1/nlq/ask ────────► Gửi câu hỏi tiếng Việt phân tích dữ liệu
  2. [Client FE] ──► GET /api/v1/manager/history ─► Xem danh sách các câu hỏi đã hỏi gần đây
  3. [Client FE] ──► GET /api/v1/manager/history/{query_id} ──► Xem lại câu trả lời cũ
```

---

## 3. Đặc tả Chi tiết từng API (Request / Response / Error)

---

### NHÓM 1: NGỮ CẢNH QUẢN LÝ (CONTEXT)

#### 1. Lấy thông tin quyền và phòng ban phụ trách của Manager
* **Endpoint:** `GET /api/v1/manager/context`
* **Quyền hạn:** `HR_MANAGER`, `ACADEMIC_MANAGER`, `ADMISSIONS_MANAGER`
* **Mục đích:** Xác định tài khoản thuộc phòng ban nào, domain quản lý nào, kiểm tra cấu hình phòng ban trước khi mở các tab nghiệp vụ.

**Request Header:**
```http
GET /api/v1/manager/context HTTP/1.1
Host: localhost:8000
Authorization: Bearer <access_token>
```

**Response `200 OK`:**
```json
{
  "user_id": 4,
  "roles": ["HR_MANAGER"],
  "domains": [
    {
      "domain": "hr",
      "name": "Nhân sự"
    }
  ],
  "departments": [
    {
      "department_id": 1,
      "department_code": "PNS",
      "department_name": "Phòng Tổ chức Nhân sự"
    }
  ],
  "default_domain": "hr",
  "needs_department_assignment": false,
  "needs_department_configuration": false,
  "data_layer": "GOLD",
  "note": "Gold chỉ đọc từ dataset đang hoạt động thuộc phòng phụ trách theo role. PNS → hr, PDT → academic, PTS → admissions."
}
```

**Mã lỗi:**
* `401 UNAUTHORIZED`: Chưa đăng nhập hoặc token hết hạn.
* `403 FORBIDDEN`: Không phải vai trò Trưởng phòng (Manager).
* `503 MANAGER_UNAVAILABLE`: Lỗi kết nối cơ sở dữ liệu.

---

### NHÓM 2: TỔNG QUAN & PHÂN TÍCH KPI GOLD

#### 2. Tổng quan phòng ban (Overview Dashboard)
* **Endpoint:** `GET /api/v1/manager/overview`
* **Quyền hạn:** Manager đúng domain
* **Mục đích:** Lấy tình trạng sẵn sàng của dữ liệu Gold, số lượng nguồn/dataset, mốc cập nhật mới nhất, số lượng lỗi chất lượng và các thẻ KPI chính.

**Query Parameters:**
| Tham số | Kiểu | Mặc định | Mô tả |
| :--- | :---: | :---: | :--- |
| `domain` | string | null | Lọc theo domain (`hr`, `academic`, `admissions`) |
| `dataset_id` | integer | null | Lọc theo một bộ dữ liệu cụ thể |
| `limit` / `offset` | integer | 20 / 0 | Phân trang cho danh sách KPIs |

**Request:**
```http
GET /api/v1/manager/overview?domain=hr HTTP/1.1
Authorization: Bearer <access_token>
```

**Response `200 OK`:**
```json
{
  "data_layer": "GOLD",
  "status": "READY",
  "datasets": 3,
  "sources": {
    "total": 3,
    "current": 3,
    "not_started": 0,
    "queued": 0,
    "running": 0,
    "retry_wait": 0,
    "failed": 0,
    "superseded": 0
  },
  "last_updated_at": "2026-11-18T10:30:00Z",
  "kpis": {
    "items": [
      {
        "dataset_id": 5,
        "dataset_name": "Hồ sơ Cán bộ - Giảng viên 2026",
        "domain": "hr",
        "scope": "production:pns-hr",
        "gold_snapshot_id": "snap-gold-001",
        "silver_snapshot_id": "snap-silver-001",
        "recipe_version": "v1",
        "finished_at": "2026-11-18T10:30:00Z",
        "entity": "employee",
        "metric": "record_count",
        "dimensions": {},
        "value": 450,
        "unit": "records"
      }
    ],
    "total": 1,
    "limit": 20,
    "offset": 0
  },
  "issues": {
    "total": 5,
    "assigned": 2,
    "pending_verification": 1,
    "verified": 2
  },
  "note": "Số bản ghi theo từng nguồn Gold; không cộng thành số người duy nhất."
}
```

*Ý nghĩa của trường `status`:*
* `READY`: Tất cả nguồn dữ liệu đã sẵn sàng trên tầng Gold (màu xanh lá).
* `PARTIAL`: Một phần nguồn đã có dữ liệu Gold (màu vàng).
* `PENDING`: Đang chờ xử lý Gold (màu cam).
* `NO_DATA`: Chưa có nguồn dữ liệu nào (màu xám).

---

#### 3. Phân tích dữ liệu chuyên sâu (Analytics Charts)
* **Endpoint:** `GET /api/v1/manager/analytics`
* **Quyền hạn:** Manager đúng domain
* **Mục đích:** Khai thác các số liệu phân nhóm nghiệp vụ (group by) để dựng biểu đồ cột/tròn.

**Query Parameters:**
| Tham số | Kiểu | Bắt buộc | Mặc định | Mô tả |
| :--- | :---: | :---: | :---: | :--- |
| `domain` | string | Không | null | `hr`, `academic`, `admissions` |
| `entity` | string | Không | null | Thực thể (VD: `employee`, `student`) |
| `metric` | string | Không | `record_count` | Mã chỉ số cần phân tích |
| `dimension_value` | string | Không | null | Lọc theo giá trị chiều (VD lọc theo phòng ban) |

*Các chỉ số tiêu biểu theo Domain:*
* **HR:** `record_count_by_employee_status` (theo trạng thái nhân sự: chính thức, thử việc...), `record_count_by_title`.
* **Academic:** `record_count_by_program_id` (sinh viên theo chương trình đào tạo), `record_count_by_academic_status`.
* **Admissions:** `record_count_by_admission_status` (hồ sơ theo trạng thái nhập học/trúng tuyển).

**Response `200 OK`:**
```json
{
  "items": [
    {
      "dataset_id": 5,
      "dataset_name": "Hồ sơ Cán bộ - Giảng viên 2026",
      "domain": "hr",
      "scope": "production:pns-hr",
      "gold_snapshot_id": "snap-gold-001",
      "silver_snapshot_id": "snap-silver-001",
      "recipe_version": "v1",
      "finished_at": "2026-11-18T10:30:00Z",
      "entity": "employee",
      "metric": "record_count_by_employee_status",
      "dimensions": {
        "employee_status": "CHINH_THUC"
      },
      "value": 290,
      "unit": "records"
    },
    {
      "dataset_id": 5,
      "dataset_name": "Hồ sơ Cán bộ - Giảng viên 2026",
      "domain": "hr",
      "scope": "production:pns-hr",
      "gold_snapshot_id": "snap-gold-001",
      "silver_snapshot_id": "snap-silver-001",
      "recipe_version": "v1",
      "finished_at": "2026-11-18T10:30:00Z",
      "entity": "employee",
      "metric": "record_count_by_employee_status",
      "dimensions": {
        "employee_status": "HOP_DONG"
      },
      "value": 160,
      "unit": "records"
    }
  ],
  "total": 2,
  "limit": 20,
  "offset": 0
}
```

---

#### 4. Tùy chọn phân tích (Analysis Options)
* **Endpoint:** `GET /api/v1/manager/analysis-options`
* **Mục đích:** Lấy danh sách các thực thể (`entities`) và danh mục các chỉ số (`metrics`) mà domain của phòng ban đang hỗ trợ để hiển thị lên dropdown bộ lọc.

**Response `200 OK`:**
```json
[
  {
    "domain": "hr",
    "entity": "employee",
    "metrics": [
      {
        "metric": "record_count",
        "name": "Tổng số lượng nhân viên"
      },
      {
        "metric": "record_count_by_employee_status",
        "name": "Phân bổ theo trạng thái làm việc"
      }
    ]
  }
]
```

---

### NHÓM 3: QUẢN LÝ BỘ DỮ LIỆU & NGUỒN GỐC (DATA CATALOG & LINEAGE)

#### 5. Danh mục bộ dữ liệu phòng ban
* **Endpoint:** `GET /api/v1/manager/catalog`
* **Quyền hạn:** Manager đúng domain
* **Mục đích:** Xem danh sách các Dataset do phòng ban mình quản lý, kèm cấu trúc bảng và thời điểm xuất bản Gold cuối.

**Query Parameters:** `limit` (20), `offset` (0), `domain` (tùy chọn).

**Response `200 OK`:**
```json
{
  "items": [
    {
      "dataset_id": 5,
      "dataset_code": "hr_employees_raw",
      "dataset_name": "Hồ sơ Cán bộ - Giảng viên 2026",
      "domain": "hr",
      "description": "Bộ dữ liệu hồ sơ nhân sự của trường",
      "updated_at": "2026-11-15T09:30:00Z",
      "layer": "BRONZE",
      "last_published_at": "2026-11-18T10:30:00Z",
      "tables": [
        {
          "table_name": "employees",
          "description": "Bảng thông tin nhân viên",
          "columns": [
            {
              "column_name": "employee_code",
              "data_type": "VARCHAR",
              "is_nullable": false,
              "description": "Mã cán bộ"
            },
            {
              "column_name": "full_name",
              "data_type": "VARCHAR",
              "is_nullable": false,
              "description": "Họ và tên"
            }
          ]
        }
      ]
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

---

#### 6. Chi tiết một bộ dữ liệu trong Catalog
* **Endpoint:** `GET /api/v1/manager/catalog/{dataset_id}`
* **Response `200 OK`:** Trả về đối tượng `ExplorerDataset` chi tiết (như 1 phần tử trong danh sách trên).

---

#### 7. Sơ đồ nguồn gốc dữ liệu (Data Lineage)
* **Endpoint:** `GET /api/v1/manager/catalog/{dataset_id}/lineage`
* **Mục đích:** Truy vết vòng đời dữ liệu từ lúc nạp tệp thô Bronze $\rightarrow$ Làm sạch Silver $\rightarrow$ Tính toán chỉ số Gold để phục vụ kiểm toán dữ liệu.

**Response `200 OK`:**
```json
{
  "dataset_id": 5,
  "steps": [
    {
      "step_name": "Tiếp nhận nguồn thô (Bronze)",
      "layer": "BRONZE",
      "artifact_uri": "s3://bronze/hr/employees_2026.csv",
      "executed_at": "2026-11-18T08:00:00Z",
      "status": "COMPLETED"
    },
    {
      "step_name": "Làm sạch & Chuẩn hóa (Silver)",
      "layer": "SILVER",
      "artifact_uri": "s3://silver/hr/snap-silver-001.parquet",
      "executed_at": "2026-11-18T09:15:00Z",
      "status": "COMPLETED"
    },
    {
      "step_name": "Tổng hợp chỉ số (Gold)",
      "layer": "GOLD",
      "artifact_uri": "s3://gold/hr/current_metrics.parquet",
      "executed_at": "2026-11-18T10:30:00Z",
      "status": "COMPLETED"
    }
  ],
  "note": "Chỉ hiển thị các bước có bằng chứng gắn với bộ dữ liệu này."
}
```

---

#### 8. Quy hoạch / Tạo mới bộ dữ liệu chuẩn
* **Endpoint:** `POST /api/v1/datasets`
* **Mục đích:** Trưởng phòng hoặc Admin chủ động tạo sẵn Bộ dữ liệu chuẩn mực để nhân viên phòng mình chỉ việc nạp file vào.

**Request Body (`application/json`):**
```json
{
  "dataset_code": "hr_contracts_2026",
  "dataset_name": "Hợp đồng Lao động Cán bộ 2026",
  "domain": "hr",
  "source_system_id": 1,
  "description": "Quản lý toàn bộ thông tin hợp đồng lao động và phụ lục"
}
```

**Response `201 Created`:**
```json
{
  "dataset_id": 9,
  "dataset_code": "hr_contracts_2026",
  "dataset_name": "Hợp đồng Lao động Cán bộ 2026",
  "domain": "hr",
  "status": "ACTIVE",
  "created_at": "2026-11-18T16:00:00Z"
}
```

---

### NHÓM 4: GIÁM SÁT CHẤT LƯỢNG DỮ LIỆU PHÒNG BAN

#### 9. Danh sách yêu cầu xử lý lỗi trong phòng
* **Endpoint:** `GET /api/v1/manager/issues`
* **Quyền hạn:** Manager đúng domain
* **Mục đích:** Xem toàn bộ các lỗi dữ liệu do nhân viên phòng mình nạp lên.

**Query Parameters:**
| Tham số | Kiểu | Mô tả |
| :--- | :---: | :--- |
| `status` | string | `ASSIGNED` (đang sửa), `PENDING_VERIFICATION` (chờ duyệt), `VERIFIED` (đã sửa xong) |
| `assignee_user_id` | integer | Lọc theo ID nhân viên đang phụ trách |
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
    "description": "Bổ sung employee_code và chuẩn hóa date_of_birth.",
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

#### 10. Thống kê số lượng yêu cầu xử lý lỗi của phòng
* **Endpoint:** `GET /api/v1/manager/issues/stats`
* **Response `200 OK`:**
```json
{
  "total": 8,
  "assigned": 4,
  "pending_verification": 2,
  "verified": 2
}
```

---

#### 11. Xem chi tiết lỗi và tiến độ xử lý của nhân viên
* **Endpoint:** `GET /api/v1/manager/issues/{issue_id}`
* **Response `200 OK`:** Trả về toàn bộ hồ sơ cách ly (`quarantine_records`), vị trí dòng lỗi, tên file gốc (`source_table`) và lịch sử thay đổi trạng thái (`history`).

---

### NHÓM 5: PHÊ DUYỆT QUY TẮC DỮ LIỆU (PROFILE GOVERNANCE / DOMAIN OWNER)

#### 12. Hàng đợi phê duyệt quy tắc chuẩn hóa (Review Queue)
* **Endpoint:** `GET /api/v1/pipeline/profiles/review-queue`
* **Quyền hạn:** Manager có quyền Domain Owner (đúng domain)
* **Mục đích:** Xem các cấu hình làm sạch/chuẩn hóa dữ liệu mới do Data Admin cấu hình đang chờ Trưởng phòng nghiệp vụ duyệt.

**Response `200 OK`:**
```json
{
  "grants": [
    {
      "grant_id": "g-hr-001",
      "domain": "hr",
      "user_id": "4",
      "granted_at": "2026-11-01T08:00:00Z"
    }
  ],
  "items": [
    {
      "profile_id": "hr_employee_clean_v2",
      "definition": {
        "domain": "hr",
        "entity": "employee",
        "description": "Chuẩn hóa định dạng CCCD 12 số và email trường"
      },
      "revision": "rev-002",
      "fingerprint": "sha256:abc123def456...",
      "status": "PENDING",
      "can_approve": true,
      "can_revoke": false
    }
  ]
}
```

---

#### 13. Lịch sử phiên bản quy tắc
* **Endpoint:** `GET /api/v1/pipeline/profiles/{profile_id}/revisions`
* **Mục đích:** Xem lại các lần thay đổi trước đây của quy tắc, ai duyệt, vào thời gian nào.

---

#### 14. Thao tác Phê duyệt quy tắc mới
* **Endpoint:** `POST /api/v1/pipeline/profiles/{profile_id}/approve`
* **Status Code:** `201 Created`
* **Mục đích:** Manager chấp thuận quy tắc chuẩn hóa để pipeline chính thức áp dụng khi làm sạch dữ liệu.

**Request Body (`application/json`):**
```json
{
  "expected_revision": "rev-002",
  "expected_fingerprint": "sha256:abc123def456...",
  "grant_id": "g-hr-001",
  "note": "Xác nhận quy tắc chuẩn hóa CCCD 12 số đúng với quy định mới của phòng Nhân sự."
}
```

**Response `201 Created`:**
```json
{
  "status": "APPROVED",
  "approved_at": "2026-11-18T16:30:00Z"
}
```

**Mã lỗi thường gặp:**
* `403 SILVER_SELF_APPROVAL_FORBIDDEN`: Người tạo cấu hình không được tự duyệt cấu hình của chính mình.
* `403 SILVER_DOMAIN_OWNER_REQUIRED`: Tài khoản không có grant Domain Owner cho domain này.
* `409 SILVER_PROFILE_REVISION_CONFLICT`: Phiên bản cấu hình đã bị thay đổi trước khi bấm duyệt.

---

#### 15. Thu hồi phê duyệt quy tắc
* **Endpoint:** `POST /api/v1/pipeline/profiles/{profile_id}/revisions/{revision}/revoke`
* **Mục đích:** Hủy bỏ quyết định phê duyệt nếu phát hiện quy tắc chưa phù hợp.

---

### NHÓM 6: HỎI ĐÁP THÔNG MINH & TRA CỨU LỊCH SỬ (UNILAKE AI)

#### 16. Hỏi đáp dữ liệu bằng ngôn ngữ tự nhiên
* **Endpoint:** `POST /api/v1/nlq/ask`
* **Request Body:**
```json
{
  "question": "Cơ cấu nhân sự theo trạng thái làm việc hiện tại?",
  "domain": "hr"
}
```
**Response `200 OK`:** Trả về câu trả lời tự nhiên dạng văn bản Markdown kèm bảng dữ liệu trích xuất từ tầng Gold.

---

#### 17. Danh sách lịch sử các câu hỏi đã tra cứu
* **Endpoint:** `GET /api/v1/manager/history`
* **Query Parameters:** `limit` (20), `offset` (0), `domain` (tùy chọn)

**Response `200 OK`:**
```json
{
  "items": [
    {
      "id": "e4f3a2b1-1234-5678-abcd-ef0123456789",
      "question": "Cơ cấu nhân sự theo trạng thái làm việc hiện tại?",
      "status": "COMPLETED",
      "domain": "hr",
      "duration_ms": 1420,
      "row_count": 4,
      "created_at": "2026-11-18T11:00:00Z",
      "error_code": null
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

---

#### 18. Xem lại kết quả phân tích cũ
* **Endpoint:** `GET /api/v1/manager/history/{query_id}`
* **Response `200 OK`:** Trả về chi tiết câu hỏi, thời gian thực thi, câu lệnh SQL phát sinh và bảng kết quả cũ đã truy vấn.

---

## 4. Bảng Tra cứu Mã Lỗi Nghiệp vụ Phổ biến cho Manager

| HTTP Status | Mã lỗi Backend (`error.code`) | Ý nghĩa & Nguyên nhân | Hướng xử lý cho Manager |
| :---: | :--- | :--- | :--- |
| `401` | `UNAUTHORIZED` / `INVALID_TOKEN` | Token hết hạn hoặc chưa đăng nhập. | Đăng nhập lại để nhận token mới. |
| `403` | `FORBIDDEN` | Tài khoản không có vai trò `*_MANAGER`. | Kiểm tra lại tài khoản đăng nhập. |
| `403` | `MANAGER_SCOPE_REQUIRED` | Manager truy cập vào dữ liệu/báo cáo của domain khác. | Chuyển về đúng phân hệ phòng ban của mình. |
| `403` | `SILVER_DOMAIN_OWNER_REQUIRED` | Không có quyền Domain Owner để duyệt cấu hình. | Yêu cầu Admin cấp grant duyệt quy tắc. |
| `403` | `SILVER_SELF_APPROVAL_FORBIDDEN` | Người sửa cấu hình không được tự phê duyệt. | Cần một tài khoản Manager khác duyệt độc lập. |
| `404` | `DATASET_NOT_FOUND` | Dataset không tồn tại hoặc thuộc phòng ban khác. | Kiểm tra lại ID trong Catalog. |
| `404` | `QUALITY_ISSUE_NOT_FOUND` | Yêu cầu sửa lỗi không thuộc phạm vi phòng ban. | Kiểm tra lại liên kết Issue. |
| `409` | `SILVER_PROFILE_REVISION_CONFLICT` | Phiên bản quy tắc đã thay đổi trước khi bấm duyệt. | Tải lại trang hàng đợi để nhận bản mới nhất. |
| `503` | `MANAGER_UNAVAILABLE` | Lỗi kết nối DB hoặc tầng Gold đang bảo trì. | Thử lại sau ít phút hoặc báo Admin. |

---

## 5. Danh sách Tài khoản Kiểm thử Mẫu (Seed Data)

* Mật khẩu mặc định cho toàn bộ tài khoản thử nghiệm: **`Password@123`**

| Tài khoản | Vai trò (`role`) | Phòng ban | Mục đích kiểm thử |
| :--- | :--- | :--- | :--- |
| `hr_manager` | `HR_MANAGER` | Phòng Tổ chức Nhân sự (`PNS`) | Kiểm thử toàn bộ màn hình Tổng quan, Phân tích, Catalog và Duyệt quy tắc Nhân sự. |
| `academic_manager` | `ACADEMIC_MANAGER` | Phòng Quản lý Đào tạo (`PDT`) | Kiểm thử phân hệ Đào tạo & chặn chéo Nhân sự. |
| `admissions_manager` | `ADMISSIONS_MANAGER` | Phòng Tuyển sinh (`PTS`) | Kiểm thử phân hệ Tuyển sinh. |
