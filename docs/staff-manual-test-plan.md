# Kế hoạch Test, Quy trình Test & Bộ Test Case Manual — Các Role STAFF

Tài liệu phục vụ **kiểm thử thủ công (Manual Testing)** cho toàn bộ chức năng và API của 3 role STAFF (`HR_STAFF`, `ACADEMIC_STAFF`, `ADMISSIONS_STAFF`) trên cả giao diện (FE) và kiểm chứng phản hồi API qua Browser DevTools / Swagger UI (`/docs`) / Postman.

- **Tài liệu API tham chiếu:** [`docs/api/staff-frontend-api.md`](api/staff-frontend-api.md) và [`docs/staff-api.md`](staff-api.md)
- **Phạm vi role kiểm thử:** `HR_STAFF` (`hr`), `ACADEMIC_STAFF` (`academic`), `ADMISSIONS_STAFF` (`admissions`)

---

## Mục lục

1. [Kế hoạch Test (Test Plan)](#1-kế-hoạch-test-test-plan)
2. [Chuẩn bị Tài khoản & Dữ liệu Test](#2-chuẩn-bị-tài-khoản--dữ-liệu-test)
3. [Quy trình Test Manual (Test Procedure)](#3-quy-trình-test-manual-test-procedure)
4. [Bộ Test Case chi tiết](#4-bộ-test-case-chi-tiết)
   - [4.1. Nhóm A — Ngữ cảnh màn hình STAFF & Bộ dữ liệu](#41-nhóm-a--ngữ-cảnh-màn-hình-staff--bộ-dữ-liệu)
   - [4.2. Nhóm B — Upload file (Tạo bộ mới & Nạp bổ sung) & Validation](#42-nhóm-b--upload-file-tạo-bộ-mới--nạp-bổ-sung--validation)
   - [4.3. Nhóm C — Theo dõi Job Ingestion](#43-nhóm-c--theo-dõi-job-ingestion)
   - [4.4. Nhóm D — Luồng Xử lý Yêu cầu Dữ liệu lỗi (Data Quality Issues)](#44-nhóm-d--luồng-xử-lý-yêu-cầu-dữ-liệu-lỗi-data-quality-issues)
   - [4.5. Nhóm E — Kiểm thử Phân quyền chéo & Chặn vượt quyền](#45-nhóm-e--kiểm-thử-phân-quyền-chéo--chặn-vượt-quyền)
5. [Biểu mẫu ghi nhận kết quả & Báo cáo lỗi (Bug Template)](#5-biểu-mẫu-ghi-nhận-kết-quả--báo-cáo-lỗi-bug-template)

---

## 1. Kế hoạch Test (Test Plan)

### 1.1. Mục tiêu
- Đảm bảo 3 role STAFF (`HR_STAFF`, `ACADEMIC_STAFF`, `ADMISSIONS_STAFF`) thực hiện đúng luồng nghiệp vụ: xem ngữ cảnh phân quyền, chọn/tạo bộ dữ liệu, tải file CSV/XLSX/XLSM lên Bronze, theo dõi trạng thái job, nhận và sửa yêu cầu dữ liệu lỗi.
- Đảm bảo cô lập dữ liệu nghiêm ngặt theo **domain** và **phòng ban/người được giao**: không xem được, không nạp được, không sửa thay được dữ liệu ngoài phạm vi.
- Đảm bảo UI hiển thị thông báo rõ ràng khi thiếu phân công phòng ban, lỗi validate file, hoặc khi bị từ chối cần nộp bản mới.

### 1.2. Phạm vi kiểm thử

| Trong phạm vi (In Scope) | Ngoài phạm vi (Out of Scope) |
| --- | --- |
| - Màn hình & API `GET /api/v1/staff/context`<br>- Chọn/tìm dataset `GET /api/v1/datasets`, `/{dataset_id}`<br>- Upload tạo mới & upload vào dataset có sẵn `POST /api/v1/ingestion/uploads`<br>- Theo dõi job, stats, metrics, attempts `GET /api/v1/ingestion/jobs*`<br>- Danh sách, thống kê, chi tiết, upload bản sửa, nộp resolution `data-quality-issues`<br>- Kiểm tra chặn vượt quyền Admin & cross-domain | - Chi tiết luồng Login/Refresh/Logout (chỉ dùng để lấy token)<br>- Các chức năng quản trị thuần túy của `ADMIN` / `DATA_ADMIN` (chỉ dùng để tạo dữ liệu test issue & verify)<br>- Kiểm thử hiệu năng tải cao (Load/Stress Test) |

### 1.3. Môi trường & Công cụ Manual Test
- **Backend API:** Đang chạy và `GET /health` trả `200 {"status": "ok"}`.
- **Ingestion Worker:** Đang chạy nền (`app.ingestion_worker`) để xử lý job từ `QUEUED` sang `SUCCESS` / `FAILED`.
- **Cơ sở dữ liệu & Kho lưu trữ:** PostgreSQL (đã chạy đủ migration & seed) và MinIO (có sẵn bucket `ingestion-uploads`, `bronze`, `silver`).
- **Công cụ cho Tester:**
  - Trình duyệt (Chrome/Edge) mở Web UI FE + tab **DevTools → Network** để đối chiếu HTTP status, payload request/response, `X-Request-ID`.
  - **Swagger UI** (`/docs`) hoặc **Postman** để test các case gửi request sai / vượt quyền mà UI đã ẩn nút.

### 1.4. Tiêu chí Bắt đầu & Kết thúc (Entry / Exit Criteria)
- **Entry Criteria:** Build mới nhất của FE & BE đã deploy; database đã seed user; worker đang hoạt động; bộ file mẫu test đã chuẩn bị xong.
- **Exit Criteria:**
  - 100% Test Case mức **Critical / High** đạt `PASS`.
  - Không còn lỗi mức `Blocker` hoặc `Critical` (đặc biệt lỗi lộ dữ liệu khác domain hoặc vượt quyền nộp sửa).
  - Tất cả lỗi phát sinh đều được log kèm `X-Request-ID`, payload và ảnh/video chụp màn hình.

---

## 2. Chuẩn bị Tài khoản & Dữ liệu Test

### 2.1. Danh sách tài khoản test (từ seed [`infra/postgres/seed/03-users-and-roles.sql`](../infra/postgres/seed/03-users-and-roles.sql))

Mật khẩu khởi tạo mặc định ở môi trường phát triển: `Password@123`.

| STT | Username | Role | Vai trò trong test | Yêu cầu DB cho luồng Issue |
| --- | --- | --- | --- | --- |
| 1 | `hr_staff` | `HR_STAFF` | Test chính lĩnh vực `hr` | Gắn `employee_id` + `app.assignment` `ACTIVE` thuộc phòng HR |
| 2 | `hr_staff_2` *(tạo thêm)* | `HR_STAFF` | Test đồng nghiệp cùng phòng HR (không phải assignee) | Cùng `department_id` với `hr_staff` |
| 3 | `academic_staff` | `ACADEMIC_STAFF` | Test chính lĩnh vực `academic` | Gắn `employee_id` + `app.assignment` `ACTIVE` thuộc phòng Đào tạo |
| 4 | `admissions_staff` | `ADMISSIONS_STAFF` | Test chính lĩnh vực `admissions` | Gắn `employee_id` + `app.assignment` `ACTIVE` thuộc phòng Tuyển sinh |
| 5 | `staff_no_dept` *(tạo thêm hoặc tài khoản seed chưa gắn `employee_id`)* | `HR_STAFF` | Test case chưa được phân công phòng ban | `employee_id IS NULL` hoặc không có `assignment` `ACTIVE` |
| 6 | `dataadmin` | `DATA_ADMIN` | Tài khoản phụ trợ: chạy Cleaning tạo quarantine, tạo issue, giao việc & verify/reject | — |
| 7 | `viewer` / `hr_manager` | `VIEWER` / `HR_MANAGER` | Test negative: role không được vào màn hình upload STAFF | — |

> **Lưu ý quan trọng:** Các tài khoản seed mặc định trong `03-users-and-roles.sql` có `employee_id = NULL`. Trước khi test **Nhóm D (Data Quality Issues)**, cần nhờ Admin/DB gắn `employee_id` (`employee_status = 'ACTIVE'`) và bản ghi `app.assignment` (`assignment_status = 'ACTIVE'`, còn hiệu lực ngày) tại `app.department` (`status = 'ACTIVE'`) cho `hr_staff`, `hr_staff_2`, `academic_staff`, `admissions_staff`.

### 2.2. Bộ file mẫu cần chuẩn bị

Lưu tại máy tester trước khi chạy test (có thể lấy từ `data/samples/` trong repo):

| Tên file mẫu | Mô tả & mục đích |
| --- | --- |
| `hr_employees_valid.csv` | File CSV UTF-8, phân cách `,`, có header, dữ liệu nhân sự hợp lệ (~5–20 dòng) |
| `academic_students_valid.xlsx` | File Excel `.xlsx` hợp lệ, sheet `Students`, dữ liệu đào tạo |
| `admissions_applicants_valid.xlsm` | File Excel `.xlsm` hợp lệ, dữ liệu tuyển sinh |
| `hr_employees_fix_v2.csv` | Bản đã sửa lỗi, **giữ đúng tên file nguồn** trong issue để test nộp resolution |
| `wrong_filename.csv` | Nội dung hợp lệ nhưng **khác tên** với `source_table` trong issue |
| `hr_invalid_rows.csv` | File CSV có dòng thiếu trường bắt buộc / sai định dạng để Admin chạy Cleaning sinh Quarantine -> tạo Issue |
| `tên file có dấu.csv` | Test lỗi tên file tiếng Việt / khoảng trắng (`UPLOAD_FILENAME_INVALID`) |
| `empty_file.csv` | File dung lượng `0 byte` (`UPLOAD_EMPTY`) |
| `unsupported_doc.pdf` | File sai định dạng không phải CSV/XLSX/XLSM (`UPLOAD_FILE_TYPE_UNSUPPORTED`) |
| `oversize_file.csv` | File vượt `max_file_bytes` cấu hình (mặc định 50 MB) (`UPLOAD_TOO_LARGE`) |

---

## 3. Quy trình Test Manual (Test Procedure)

Thực hiện tuần tự theo 6 giai đoạn sau cho mỗi đợt test (Regression / Release):

### Giai đoạn 1 — Kiểm tra hệ thống & chuẩn bị môi trường (5 phút)
1. Gọi `GET /health` -> xác nhận trả về `200 {"status": "ok"}`.
2. Kiểm tra worker đang chạy (hoặc Admin gọi `GET /api/v1/ingestion/workers` thấy worker `HEALTHY`).
3. Đăng nhập lấy token riêng cho `hr_staff`, `academic_staff`, `admissions_staff`, `dataadmin`, `viewer` (mở các browser profile / tab ẩn danh riêng hoặc lưu token vào biến môi trường Postman).

### Giai đoạn 2 — Smoke Test theo từng Role STAFF (15 phút)
Thực hiện lặp lại với **cả 3 role** (`hr_staff`, `academic_staff`, `admissions_staff`):
1. Mở giao diện STAFF -> kiểm tra `GET /api/v1/staff/context` trả đúng `domain` (`hr` / `academic` / `admissions`), `default_domain`, `departments`.
2. Gọi/mở danh sách dataset -> xác nhận chỉ thấy dataset của đúng domain mình.
3. Upload 1 file hợp lệ dạng **tạo bộ mới** -> xác nhận trả `202` -> theo dõi polling job tự chuyển `QUEUED` -> `RUNNING` -> `SUCCESS`.
4. Upload 1 file hợp lệ dạng **nạp tiếp vào dataset vừa tạo** -> xác nhận không tạo trùng dataset, job chạy `SUCCESS`.

### Giai đoạn 3 — Test Validation & Negative Upload (15 phút)
1. Thực hiện upload các file lỗi: sai đuôi (`.pdf`), 0 byte, tên có dấu, vượt dung lượng, JSON `parser_options` sai, gửi trùng cả `dataset_id` và `dataset_name`, trùng tên dataset mới.
2. Kiểm tra trên Network tab đúng mã HTTP (`409`, `413`, `415`, `422`) và trên DB/danh sách job **không sinh ra job rác hay dataset rác**.
3. Upload file có dữ liệu lỗi bên trong -> job tạo thành công (`202`) nhưng worker đánh dấu `FAILED`/`REJECTED` kèm `validation_report` / `error_message` hiển thị rõ trên UI.

### Giai đoạn 4 — Test End-to-End Luồng Data Quality Issues (25 phút)
1. **Setup bởi `dataadmin`:** Upload file có dòng lỗi (hoặc dùng job lỗi từ Giai đoạn 3) -> chạy Cleaning (`POST /api/v1/pipeline/cleaning/ingestion-jobs/{id}`) để phát sinh bản ghi Quarantine -> tạo Issue (`POST /api/v1/data-quality-issues`) giao cho `hr_staff` (lặp lại tối thiểu 1 luồng đầy đủ cho `hr_staff`, và smoke 1 luồng cho `academic_staff` / `admissions_staff`).
2. **STAFF nhận việc:** Đăng nhập `hr_staff` -> kiểm tra thống kê `/stats`, lọc `mine_only=true&status=ASSIGNED` -> mở chi tiết issue, đối chiếu `locator`, `issues`, `source_table`.
3. **STAFF sửa & nộp sai (Negative):** Thử upload sai tên file; thử nộp `resolution` bằng job chưa `SUCCESS` hoặc job của file khác -> xác nhận đều bị chặn (`422`) và trạng thái issue vẫn là `ASSIGNED`.
4. **STAFF nộp đúng:** Upload đúng tên file đã sửa qua `POST /{issue_id}/uploads` -> chờ polling job đạt `SUCCESS` -> gửi `POST /{issue_id}/resolution` -> xác nhận trạng thái chuyển `PENDING_VERIFICATION` và `history` ghi `RESOLUTION_SUBMITTED`.
5. **Vòng từ chối & nộp lại:**
   - `dataadmin` chạy Cleaning cho job sửa -> gọi `POST /{issue_id}/verification` với `verified: false` -> issue quay về `ASSIGNED`.
   - `hr_staff` thử nộp lại chính `ingestion_job_id` cũ -> phải bị từ chối (`422`).
   - `hr_staff` upload một bản file mới khác -> chờ `SUCCESS` -> nộp `resolution` mới -> `dataadmin` chạy Cleaning và gọi `verification` với `verified: true` -> issue chuyển `VERIFIED`.
   - `hr_staff` kiểm tra lại ở trạng thái `VERIFIED` không thể upload/submit resolution thêm nữa (`409`).

### Giai đoạn 5 — Test Phân quyền chéo & Bảo mật (15 phút)
Thực hiện bằng Postman/Swagger với token của STAFF:
1. Thử truy cập chéo domain: `hr_staff` gọi `GET /datasets/{academic_id}`, `POST /ingestion/uploads` với `dataset_id` của `academic`, `GET /ingestion/jobs/{academic_job_id}`, `GET /data-quality-issues/{academic_issue_id}`.
2. Thử đồng nghiệp sửa thay: `hr_staff_2` (cùng phòng HR) xem được issue của `hr_staff`, nhưng thử `POST /{issue_id}/uploads` hoặc `/resolution` -> phải bị chặn `403`.
3. Thử gọi các API của Admin (`POST /datasets`, `POST /ingestion/jobs`, `retry`, `workers`, `pipeline/cleaning`, `quarantine-records`, `governance`, tạo/giao/verify issue) -> tất cả phải trả `403`.

### Giai đoạn 6 — Nghiệm thu & Báo cáo
1. Tổng hợp bảng Pass/Fail theo từng nhóm TC.
2. Ghi nhận tất cả lỗi theo mẫu ở [Mục 5](#5-biểu-mẫu-ghi-nhận-kết-quả--báo-cáo-lỗi-bug-template).

---

## 4. Bộ Test Case chi tiết

### 4.1. Nhóm A — Ngữ cảnh màn hình STAFF & Bộ dữ liệu

| ID | Tên Test Case | Role test | Điều kiện tiên quyết | Các bước thực hiện | Kết quả mong đợi |
| --- | --- | --- | --- | --- | --- |
| **TC-A01** | Kiểm tra `staff/context` cho `HR_STAFF` đã phân công | `HR_STAFF` (`hr_staff`) | Tài khoản có `employee_id` & `assignment` `ACTIVE` ở phòng HR | 1. Đăng nhập `hr_staff`.<br>2. Mở màn hình STAFF (gọi `GET /api/v1/staff/context`). | - HTTP `200`.<br>- `roles` chứa `HR_STAFF`.<br>- `domains` chỉ có `[{"domain": "hr", "name": "Nhân sự"}]`, `default_domain = "hr"`.<br>- `departments` có thông tin phòng HR.<br>- `can_upload = true`, `can_resolve_issues = true`, `can_manage_silver = false`, `needs_department_assignment = false`. |
| **TC-A02** | Kiểm tra `staff/context` cho `ACADEMIC_STAFF` đã phân công | `ACADEMIC_STAFF` (`academic_staff`) | Tài khoản có `employee_id` & `assignment` `ACTIVE` ở phòng Đào tạo | 1. Đăng nhập `academic_staff`.<br>2. Mở màn hình STAFF (`GET /api/v1/staff/context`). | - HTTP `200`.<br>- `domains` chỉ có `academic` ("Đào tạo"), `default_domain = "academic"`.<br>- `can_upload = true`, `can_resolve_issues = true`, `can_manage_silver = false`. |
| **TC-A03** | Kiểm tra `staff/context` cho `ADMISSIONS_STAFF` đã phân công | `ADMISSIONS_STAFF` (`admissions_staff`) | Tài khoản có `employee_id` & `assignment` `ACTIVE` ở phòng Tuyển sinh | 1. Đăng nhập `admissions_staff`.<br>2. Mở màn hình STAFF (`GET /api/v1/staff/context`). | - HTTP `200`.<br>- `domains` chỉ có `admissions` ("Tuyển sinh"), `default_domain = "admissions"`.<br>- `can_upload = true`, `can_resolve_issues = true`, `can_manage_silver = false`. |
| **TC-A04** | STAFF chưa được phân công phòng ban | `HR_STAFF` (`staff_no_dept`) | Tài khoản có role `HR_STAFF` nhưng `employee_id = NULL` hoặc hết hạn `assignment` | 1. Đăng nhập `staff_no_dept`.<br>2. Mở màn hình STAFF (`GET /api/v1/staff/context`). | - HTTP `200`.<br>- `departments = []`.<br>- `can_upload = true`, `can_resolve_issues = false`, `needs_department_assignment = true`.<br>- UI hiển thị cảnh báo cần phân công phòng ban trước khi xử lý issue, nhưng vẫn cho phép upload file. |
| **TC-A05** | Role không phải STAFF (`VIEWER`, `HR_MANAGER`) gọi `staff/context` | `VIEWER`, `HR_MANAGER` | Có token của `viewer` hoặc `hr_manager` | Gọi `GET /api/v1/staff/context`. | - HTTP `403`, `error.code = "FORBIDDEN"`. |
| **TC-A06** | Liệt kê & tìm kiếm dataset đúng domain | Cả 3 role STAFF | Có sẵn dataset tầng `BRONZE` thuộc cả 3 domain (`hr`, `academic`, `admissions`) | 1. Đăng nhập từng role STAFF.<br>2. Gọi `GET /api/v1/datasets?limit=20&offset=0`.<br>3. Nhập từ khóa tìm kiếm theo tên (`search=...`), thử nhập ký tự `%`. | - HTTP `200`.<br>- Mỗi role chỉ thấy dataset `ACTIVE`, tầng `BRONZE` thuộc đúng domain của mình.<br>- Nhập `%` không làm trả về toàn bộ dữ liệu ngoài từ khóa. |
| **TC-A07** | Xem chi tiết dataset hợp lệ vs khác domain | `HR_STAFF` | Biết ID 1 dataset `hr` và ID 1 dataset `academic` | 1. Gọi `GET /api/v1/datasets/{hr_dataset_id}`.<br>2. Gọi `GET /api/v1/datasets/{academic_dataset_id}`. | - Bước 1: `200 OK`, trả metadata dataset `hr`.<br>- Bước 2: `404`, `error.code = "DATASET_NOT_FOUND"` (không tiết lộ dataset lĩnh vực khác). |

---

### 4.2. Nhóm B — Upload file (Tạo bộ mới & Nạp bổ sung) & Validation

| ID | Tên Test Case | Role test | Điều kiện tiên quyết | Các bước thực hiện | Kết quả mong đợi |
| --- | --- | --- | --- | --- | --- |
| **TC-B01** | Upload lần đầu tạo bộ dữ liệu mới (CSV) | Cả 3 role STAFF (lặp cho từng role) | Chuẩn bị file CSV hợp lệ theo từng domain | 1. Tại màn hình Upload, chọn chế độ "Tạo bộ dữ liệu mới".<br>2. Chọn file CSV hợp lệ, nhập `dataset_name = "Test_Moi_<domain>"`, mô tả tùy chọn.<br>3. Không gửi `dataset_id` và không gửi `domain` (do chỉ có 1 domain).<br>4. Nhấn Upload. | - `POST /api/v1/ingestion/uploads` trả `202 Accepted`.<br>- `status = "QUEUED"`, `job_type = "UPLOAD_V1"`, trả về `ingestion_job_id` và `dataset_id` mới.<br>- Gọi `GET /api/v1/datasets` thấy bộ dữ liệu mới nằm đúng domain của role đang đăng nhập. |
| **TC-B02** | Upload nạp bổ sung vào bộ dữ liệu đã có (Excel `.xlsx` / `.xlsm`) | `ACADEMIC_STAFF`, `ADMISSIONS_STAFF` | Đã có `dataset_id` tầng `BRONZE` đúng domain | 1. Chọn chế độ "Chọn bộ dữ liệu có sẵn" -> chọn từ dropdown.<br>2. Chọn file `.xlsx` (hoặc `.xlsm`), nhập `parser_options` (ví dụ `{"sheet_name": "Students", "header_row": 1}`).<br>3. Kiểm tra DevTools chỉ gửi `dataset_id`, `file`, `parser_options` (không gửi `dataset_name`, `domain`). | - HTTP `202 Accepted`.<br>- Job dùng lại đúng `dataset_id` và `data_source_id` quản lý upload trước đó (không tạo dataset trùng). |
| **TC-B03** | Tạo mới dataset bị trùng tên trong cùng domain | `HR_STAFF` | Đã tồn tại dataset tên `"Danh sach nhan su"` trong domain `hr` | Gửi `POST /api/v1/ingestion/uploads` tạo mới với `dataset_name = "danh sach  nhan su"` (khác hoa thường/nhiều khoảng trắng). | - HTTP `409`, `error.code = "UPLOAD_DATASET_NAME_CONFLICT"`.<br>- Không tạo job mới, không tạo dataset trùng. |
| **TC-B04** | Gửi sai kết hợp đích upload (`dataset_id` và `dataset_name`) | `HR_STAFF` | Có `dataset_id` hợp lệ | 1. Gửi cả `dataset_id` và `dataset_name` cùng lúc.<br>2. Thử không gửi cả `dataset_id` lẫn `dataset_name`. | - Cả 2 lần đều trả `422`, `error.code = "UPLOAD_DESTINATION_INVALID"`. |
| **TC-B05** | Upload file sai định dạng (`.pdf`, `.txt`, `.json`) | `HR_STAFF` | File `unsupported_doc.pdf` | Chọn file `.pdf` và bấm Upload (hoặc gửi qua Postman). | - HTTP `415`, `error.code = "UPLOAD_FILE_TYPE_UNSUPPORTED"`.<br>- Không tạo job/dataset. |
| **TC-B06** | Upload file rỗng (`0 byte`) | `ACADEMIC_STAFF` | File `empty_file.csv` (0 byte) | Chọn `empty_file.csv` và bấm Upload. | - HTTP `422`, `error.code = "UPLOAD_EMPTY"`.<br>- Không tạo job/dataset. |
| **TC-B07** | Upload file tên có dấu tiếng Việt / ký tự đặc biệt | `ADMISSIONS_STAFF` | File `tên file có dấu.csv` | Chọn file `tên file có dấu.csv` và bấm Upload. | - HTTP `422`, `error.code = "UPLOAD_FILENAME_INVALID"`.<br>- UI nhắc người dùng đổi tên file không dấu, không khoảng trắng. |
| **TC-B08** | Upload file vượt dung lượng cho phép (`> max_file_bytes`) | `HR_STAFF` | File `oversize_file.csv` (> 50 MB) | Chọn file quá dung lượng và bấm Upload. | - HTTP `413`, `error.code = "UPLOAD_TOO_LARGE"`.<br>- Không lưu file lên MinIO, không tạo job. |
| **TC-B09** | `parser_options` không hợp lệ | `HR_STAFF` | File CSV hợp lệ | 1. Gửi `parser_options = "{invalid_json"`.<br>2. Gửi `parser_options = '{"delimiter": ",", "quote_char": ","}'`. | - HTTP `422`, `error.code = "UPLOAD_PARSER_INVALID"`. |
| **TC-B10** | Upload file CSV hợp lệ đuôi `.csv` nhưng nội dung lỗi dữ liệu/cấu trúc | `HR_STAFF` | File `hr_invalid_rows.csv` (sai định dạng cột / thiếu dữ liệu bắt buộc) | 1. Upload file `hr_invalid_rows.csv` -> nhận `202` (`job_id`).<br>2. Chờ worker xử lý và gọi `GET /api/v1/ingestion/jobs/{job_id}`. | - Bước 1: `202 Accepted`, `QUEUED`.<br>- Bước 2: Sau vài giây job chuyển `FAILED` (hoặc `REJECTED`), `error_message` có mã lỗi validation, không ghi bản lỗi vào Bronze. |
| **TC-B11** | STAFF cố tình upload vào `dataset_id` hoặc tạo mới ở domain khác | `HR_STAFF` | Biết `dataset_id` thuộc domain `academic` | 1. Gửi `POST /api/v1/ingestion/uploads` với `dataset_id` của `academic`.<br>2. Gửi tạo mới với `domain = "admissions"`. | - Cả 2 trường hợp trả `403`, `error.code = "UPLOAD_DOMAIN_FORBIDDEN"`. |

---

### 4.3. Nhóm C — Theo dõi Job Ingestion

| ID | Tên Test Case | Role test | Điều kiện tiên quyết | Các bước thực hiện | Kết quả mong đợi |
| --- | --- | --- | --- | --- | --- |
| **TC-C01** | Polling trạng thái job đến khi `SUCCESS` | Cả 3 role STAFF | Đã thực hiện upload file hợp lệ ở Nhóm B | 1. Mở màn hình chi tiết job hoặc theo dõi `GET /api/v1/ingestion/jobs/{job_id}` mỗi 3–5 giây.<br>2. Kiểm tra dữ liệu sau khi hoàn tất. | - Trạng thái chuyển `QUEUED` -> `RUNNING` -> `SUCCESS`.<br>- Có `rows_read`, `rows_written` khớp số dòng file; `validation_report.can_accept = true`.<br>- UI hiển thị thông báo "Đã lưu Bronze — chờ xử lý Silver". |
| **TC-C02** | Lọc, phân trang danh sách job & đối chiếu thống kê | `HR_STAFF` | Có ít nhất 2 job thuộc `hr` (do mình tạo & đồng nghiệp tạo nếu có) và job ở domain khác | 1. Gọi `GET /api/v1/ingestion/jobs?mine_only=true&status=SUCCESS&limit=10&offset=0`.<br>2. Gọi `GET /api/v1/ingestion/jobs/stats` và `/metrics`. | - Danh sách chỉ trả job thuộc domain `hr`; khi `mine_only=true` chỉ trả job của `hr_staff`.<br>- `stats.total` bằng tổng số job thuộc domain `hr` (không tính job của `academic` / `admissions`).<br>- `metrics` trả số hợp lệ hoặc `null`. |
| **TC-C03** | Xem lịch sử các lần chạy thử (`/attempts`) | `HR_STAFF` | Có `job_id` thuộc domain `hr` | Gọi `GET /api/v1/ingestion/jobs/{job_id}/attempts`. | - HTTP `200`, trả danh sách các attempt theo `attempt_number` tăng dần kèm `status`, `stage`, `error_message`. |
| **TC-C04** | Xem chi tiết hoặc attempts của job thuộc domain khác | `HR_STAFF` | Biết `job_id` của `academic_staff` | 1. Gọi `GET /api/v1/ingestion/jobs/{academic_job_id}`.<br>2. Gọi `GET /api/v1/ingestion/jobs/{academic_job_id}/attempts`. | - Cả 2 API trả `404`, `error.code = "INGESTION_JOB_NOT_FOUND"`. |

---

### 4.4. Nhóm D — Luồng Xử lý Yêu cầu Dữ liệu lỗi (Data Quality Issues)

*(Tiền đề: `dataadmin` đã chạy Cleaning phát sinh Quarantine và tạo Issue ở trạng thái `ASSIGNED`, giao cho `hr_staff` / `academic_staff` / `admissions_staff`.)*

| ID | Tên Test Case | Role test | Điều kiện tiên quyết | Các bước thực hiện | Kết quả mong đợi |
| --- | --- | --- | --- | --- | --- |
| **TC-D01** | Xem danh sách, thống kê và chi tiết issue được giao | `HR_STAFF`, `ACADEMIC_STAFF`, `ADMISSIONS_STAFF` | Đã có ít nhất 1 issue giao cho user ở trạng thái `ASSIGNED` | 1. Gọi `GET /api/v1/data-quality-issues?mine_only=true&status=ASSIGNED`.<br>2. Gọi `GET /api/v1/data-quality-issues/stats?mine_only=true`.<br>3. Nhấn vào 1 issue -> `GET /api/v1/data-quality-issues/{issue_id}`. | - HTTP `200`.<br>- Thống kê khớp phạm vi lọc.<br>- Chi tiết hiển thị rõ `quarantine_records` (gồm `locator`, `issues`, `entity`, `source_table` — tên file bắt buộc) và `history` (`ASSIGNED`). |
| **TC-D02** | STAFF chưa có phân công phòng ban truy cập màn hình Issue | `HR_STAFF` (`staff_no_dept`) | `staff_no_dept` chưa có `assignment` `ACTIVE` | Gọi `GET /api/v1/data-quality-issues` hoặc mở tab Yêu cầu sửa lỗi. | - Không xem được issue nào (`[]` hoặc UI ẩn dựa vào `can_resolve_issues = false` từ `/staff/context`). |
| **TC-D03** | Tải lên file sửa **sai tên file** so với `source_table` | `HR_STAFF` (`hr_staff`) | Issue đang `ASSIGNED` giao cho `hr_staff`, `source_table = "hr_employees.csv"` | Upload file `wrong_filename.csv` vào `POST /api/v1/data-quality-issues/{issue_id}/uploads`. | - HTTP `422`, `error.code = "RESOLUTION_FILE_MISMATCH"`.<br>- Trạng thái issue giữ nguyên `ASSIGNED`. |
| **TC-D04** | Tải lên file đã sửa **đúng tên file** trong issue | `HR_STAFF` (`hr_staff`) | Issue đang `ASSIGNED` giao cho `hr_staff` | 1. Upload file đã sửa (giữ nguyên tên `source_table`, bỏ trống `parser_options`) qua `POST /api/v1/data-quality-issues/{issue_id}/uploads`.<br>2. Polling `GET /api/v1/ingestion/jobs/{job_id}` đến khi `SUCCESS`. | - Bước 1: `202 Accepted`, response có `quality_issue_id = issue_id`. Issue vẫn là `ASSIGNED`.<br>- Bước 2: Job chuyển `SUCCESS`. |
| **TC-D05** | Đồng nghiệp cùng phòng ban hoặc STAFF khác phòng ban thao tác thay | `HR_STAFF` (`hr_staff_2`), `ACADEMIC_STAFF` | Issue giao cho `hr_staff` | 1. `hr_staff_2` (cùng phòng HR) gọi `GET /{issue_id}` -> xem được.<br>2. `hr_staff_2` gọi `POST /{issue_id}/uploads` hoặc `POST /{issue_id}/resolution`.<br>3. `academic_staff` gọi `GET /{issue_id}`, `POST /{issue_id}/uploads`. | - Bước 1: `200 OK` (chỉ được xem trong phòng).<br>- Bước 2: `403`, `error.code = "FORBIDDEN"`.<br>- Bước 3: `404`, `error.code = "QUALITY_ISSUE_NOT_FOUND"`. |
| **TC-D06** | Báo đã sửa xong với job không hợp lệ | `HR_STAFF` (`hr_staff`) | Issue đang `ASSIGNED` | 1. Gửi `POST /{issue_id}/resolution` với `job_id` đang `FAILED` hoặc `QUEUED`.<br>2. Gửi với `job_id` `SUCCESS` nhưng là của file khác hoặc job cũ có trước thời điểm phát sinh lỗi. | - Tất cả trả `422`, `error.code = "INVALID_RESOLUTION_JOB"`.<br>- Issue vẫn giữ `ASSIGNED`. |
| **TC-D07** | Báo đã sửa xong hợp lệ (`submit resolution`) | `HR_STAFF` (`hr_staff`) | Đã có job upload sửa ở TC-D04 đạt `SUCCESS` | Gửi `POST /api/v1/data-quality-issues/{issue_id}/resolution` với `{"ingestion_job_id": <job_id>, "note": "Đã sửa đủ các dòng lỗi"}`. | - HTTP `200`.<br>- `status` chuyển thành `"PENDING_VERIFICATION"`, `resolution_job_id = <job_id>`.<br>- `history` thêm sự kiện `"RESOLUTION_SUBMITTED"`. |
| **TC-D08** | Thử upload sửa hoặc nộp lại khi đang `PENDING_VERIFICATION` / `VERIFIED` | `HR_STAFF` (`hr_staff`) | Issue đang ở `PENDING_VERIFICATION` (hoặc `VERIFIED`) | Gọi `POST /{issue_id}/uploads` và `POST /{issue_id}/resolution`. | - HTTP `409`, `error.code = "QUALITY_ISSUE_STATE_CONFLICT"`. |
| **TC-D09** | Vòng Admin từ chối -> STAFF phải upload bản mới -> Admin duyệt `VERIFIED` | `HR_STAFF` + `DATA_ADMIN` | Issue ở `PENDING_VERIFICATION` | 1. `dataadmin` chạy Cleaning cho job sửa -> gọi `POST /{issue_id}/verification` với `verified: false` -> Issue quay lại `ASSIGNED`.<br>2. `hr_staff` thử nộp lại chính `job_id` của lần trước.<br>3. `hr_staff` upload bản file mới khác, chờ `SUCCESS`, rồi nộp `resolution` với `job_id` mới.<br>4. `dataadmin` chạy Cleaning & gọi `verification` với `verified: true`. | - Bước 1: `history` có `VERIFICATION_REJECTED`, `status = "ASSIGNED"`.<br>- Bước 2: Bị chặn `422 INVALID_RESOLUTION_JOB` (không cho dùng lại job cũ).<br>- Bước 3: Chuyển `PENDING_VERIFICATION`.<br>- Bước 4: Chuyển `VERIFIED`, `history` có `VERIFIED`. |

---

### 4.5. Nhóm E — Kiểm thử Phân quyền chéo & Chặn vượt quyền

| ID | Tên Test Case | Role test | Các bước thực hiện (dùng Postman / Swagger với token STAFF) | Kết quả mong đợi |
| --- | --- | --- | --- | --- |
| **TC-E01** | STAFF gọi các API quản trị Source & Dataset | `HR_STAFF`, `ACADEMIC_STAFF`, `ADMISSIONS_STAFF` | Gọi:<br>- `GET /api/v1/datasources`<br>- `POST /api/v1/datasources/create`<br>- `GET /api/v1/source-systems`<br>- `POST /api/v1/datasets` | - Tất cả trả HTTP `403`, `error.code = "FORBIDDEN"`. |
| **TC-E02** | STAFF gọi các API điều hành Ingestion của Admin | `HR_STAFF`, `ACADEMIC_STAFF`, `ADMISSIONS_STAFF` | Gọi:<br>- `POST /api/v1/ingestion/jobs`<br>- `POST /api/v1/ingestion/jobs/{id}/retry`<br>- `GET /api/v1/ingestion/workers` | - Tất cả trả HTTP `403`, `error.code = "FORBIDDEN"`. |
| **TC-E03** | STAFF gọi các API Cleaning, Silver, Quarantine, Governance | `HR_STAFF` | Gọi:<br>- `POST /api/v1/pipeline/cleaning/ingestion-jobs/{id}/preview`<br>- `POST /api/v1/pipeline/cleaning/ingestion-jobs/{id}`<br>- `GET /api/v1/pipeline/profiles`<br>- `GET /api/v1/pipeline/silver/snapshots?profile_id=...`<br>- `GET /api/v1/cleaning-runs`<br>- `GET /api/v1/quarantine-records`<br>- `GET /api/v1/governance/catalog` | - Tất cả trả HTTP `403`, `error.code = "FORBIDDEN"`. |
| **TC-E04** | STAFF gọi các API quản trị Issue của Admin | `HR_STAFF` | Gọi:<br>- `GET /api/v1/data-quality-issues/departments`<br>- `GET /api/v1/data-quality-issues/assignees?department_id=1&domain=hr`<br>- `POST /api/v1/data-quality-issues`<br>- `PATCH /api/v1/data-quality-issues/{id}/assignment`<br>- `POST /api/v1/data-quality-issues/{id}/verification` | - Tất cả trả HTTP `403`, `error.code = "FORBIDDEN"`. |
| **TC-E05** | Gọi API khi thiếu token hoặc token hết hạn | Không token / Token hết hạn | Gọi bất kỳ API STAFF (ví dụ `GET /api/v1/staff/context`) không có header `Authorization` hoặc token hết hạn. | - HTTP `401`, `error.code = "UNAUTHORIZED"` hoặc `"INVALID_TOKEN"`.<br>- UI tự refresh token hoặc chuyển về màn hình đăng nhập. |

---

## 5. Biểu mẫu ghi nhận kết quả & Báo cáo lỗi (Bug Template)

Khi phát hiện lỗi trong quá trình Manual Test, Tester ghi nhận đầy đủ các thông tin sau để Dev BE/FE xử lý nhanh:

```markdown
- **Mã Test Case:** TC-xxx
- **Môi trường:** Local / Staging / Render (`URL`)
- **Role / Tài khoản test:** (VD: `hr_staff` / `HR_STAFF`)
- **API bị lỗi:** `METHOD /api/v1/...`
- **X-Request-ID (từ Response Header hoặc `error.request_id`):** ...
- **Các bước tái hiện:**
  1. ...
  2. ...
- **Dữ liệu đầu vào / File đính kèm:** (Tên file, payload JSON)
- **Kết quả thực tế (HTTP status + JSON response / ảnh chụp UI):** ...
- **Kết quả mong đợi:** ...
- **Mức độ nghiêm trọng:** Blocker / Critical / Major / Minor
```
