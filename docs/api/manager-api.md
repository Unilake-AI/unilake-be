# API màn hình Manager

Tiền tố `/api/v1/manager`, xác thực Bearer token hiện tại. Ba role được hỗ trợ: `HR_MANAGER`, `ACADEMIC_MANAGER`, `ADMISSIONS_MANAGER`. Các API chỉ đọc; không yêu cầu thêm migration ngoài cấu trúc Gold và danh mục phòng ban hiện có.

## Phạm vi truy cập

Backend đối chiếu role hiện hành trong PostgreSQL với token; role xác định trực tiếp phòng phụ trách:

| Role | Lĩnh vực | Mã phòng phụ trách |
| --- | --- | --- |
| HR_MANAGER | hr | PNS |
| ACADEMIC_MANAGER | academic | PDT |
| ADMISSIONS_MANAGER | admissions | PTS |

Tài khoản và phòng phụ trách phải đang hoạt động. Manager không cần liên kết `employee_id` hoặc có dòng trong `app.assignment`. Tài khoản có nhiều role manager được hợp đúng các cặp lĩnh vực/phòng theo bảng trên, không ghép chéo quyền. Thu hồi role trong DB có hiệu lực ngay với các request tiếp theo, token cũ không giữ được quyền.

Dataset phải đang `ACTIVE`, có `domain` phù hợp và `owner_department_id` trùng phòng phụ trách theo role. Dataset chưa gán phòng sở hữu không được hiển thị. Quyền này giới hạn theo **phòng sở hữu dataset**, không phải lọc từng người/bản ghi trong dataset theo phòng công tác. Gold hiện chưa có tổng hợp đa chiều để thực hiện kiểu lọc bản ghi đó.

`ADMIN`, `DATA_ADMIN`, `VIEWER`, STAFF không tự có quyền vào màn hình manager. Nếu có thêm role manager, các API này vẫn dùng phạm vi manager. API `/pipeline/gold/*` cũng kiểm tra role/phòng sở hữu đối với lĩnh vực của manager để không đi vòng qua đường đọc Gold cũ; ADMIN/DATA_ADMIN/VIEWER vẫn giữ quyền tổng hợp vốn có. Không thêm quyền ghi hay duyệt công bố cho manager.

## Endpoint

| Method và đường dẫn | Kết quả |
| --- | --- |
| `GET /context` | Role, lĩnh vực, phòng hợp lệ, `default_domain`, `needs_department_configuration` |
| `GET /overview` | Tổng quan số nguồn, trạng thái cập nhật, KPI Gold theo nguồn và thống kê yêu cầu sửa |
| `GET /analytics` | Chỉ số và nhóm thống kê từ Gold hiện hành |
| `GET /datasets` | Dataset trong phạm vi và trạng thái Gold của từng nguồn Silver |
| `GET /datasets/{dataset_id}` | Chi tiết dataset cùng các nguồn Gold; ngoài phạm vi trả 404 |
| `GET /issues` | Yêu cầu sửa của phòng phụ trách trong lĩnh vực có quyền |
| `GET /issues/stats` | Tổng số yêu cầu theo trạng thái |
| `GET /issues/{issue_id}` | Chi tiết yêu cầu, vị trí/lý do lỗi, lịch sử; ngoài phạm vi trả 404 |

### Bộ lọc báo cáo

`overview`, `datasets`, `analytics`: `domain`, `dataset_id`, `limit` (1–100, mặc định 20), `offset` (>=0).

`analytics` có thêm `entity` và `metric` (mặc định `record_count`). Ví dụ:

```http
GET /api/v1/manager/overview?domain=hr
GET /api/v1/manager/analytics?domain=hr&entity=employee&metric=record_count_by_employee_status
GET /api/v1/manager/analytics?domain=academic&entity=student&metric=record_count_by_program_id
GET /api/v1/manager/analytics?domain=admissions&entity=admission&metric=record_count_by_admission_status
```

Các phép nhóm được hỗ trợ theo [quy định Gold hiện tại](../gold-pipeline.md#gold-bản-đầu-tổng-hợp-gì): `record_count` cho tổng bản ghi mỗi loại dữ liệu; `record_count_by_<tên_cột>` cho từng cột đã được Gold tổng hợp. Một dòng nhóm chứa một giá trị `dimensions`; giá trị thiếu thông tin giữ nguyên `null`. Cột không có ở nguồn không có số liệu nhóm. Chỉ số không có dữ liệu trả danh sách rỗng.

`analytics`, `datasets`, và trường `kpis` trong `overview` trả `{items, total, limit, offset}`. `total` đếm tất cả kết quả đã lọc, kể cả khi `offset` vượt cuối danh sách. `limit/offset` của `overview` chỉ phân trang KPI; số dataset, nguồn, yêu cầu sửa và thời điểm cập nhật vẫn tính trên toàn phạm vi `domain/dataset_id` đã chọn.

`issues` và `issues/stats` nhận `domain`, `dataset_id`, `department_id`, `assignee_user_id`, `status`, `mine_only`, `limit`, `offset` (theo hợp đồng issue hiện có). `status`: `ASSIGNED`, `PENDING_VERIFICATION`, `VERIFIED`. `issues` trả danh sách; `issues/stats` không chịu tác động của phân trang. Manager được xem cả yêu cầu trả lại người nạp thuộc đúng cặp lĩnh vực/phòng phụ trách, dù không phải người được giao; các thao tác sửa/giao việc tiếp tục theo quyền của API hiện có.

Gửi `domain` không được cấp trả 403. Filter không hỗ trợ (ví dụ `from_date`, `semester_id`) trả 422, không âm thầm bỏ qua. Gold hiện chỉ tổng hợp riêng từng cột, chưa hỗ trợ lọc một nhóm theo nhóm khác hoặc theo khoảng ngày nghiệp vụ.

## Nguồn và cách hiển thị

- KPI/biểu đồ: đọc trực tiếp view `gold.current_metrics` trong PostgreSQL; không đọc Bronze/Silver để tự tính lại.
- Thông tin quyền, dataset, tiến độ Gold và yêu cầu sửa: đọc các bảng vận hành `app.*`.
- Giữ riêng `dataset_id`, `scope`, `entity`, phiên bản Silver/Gold và `finished_at` cho từng chỉ số. Đơn vị `records` là số bản ghi, không mặc định là số người duy nhất. Không tự cộng các nguồn có thể trùng người.
- `last_updated_at` là thời điểm công bố mới nhất trong các số liệu Gold hiện hành được phép xem; từng KPI vẫn có `finished_at` riêng.

Trạng thái `overview.status`:

| Trạng thái | Ý nghĩa |
| --- | --- |
| `NO_DATA` | Chưa có nguồn Silver đã công bố thuộc phạm vi, KPI rỗng |
| `PENDING` | Có nguồn nhưng chưa nguồn nào có Gold hiện hành; xem `sources.failed/queued/running/...` để phân biệt nguyên nhân |
| `PARTIAL` | Một số nguồn đã có Gold hiện hành, số khác chưa sẵn sàng |
| `READY` | Tất cả nguồn Silver trong phạm vi đã có Gold hiện hành |

Mỗi dataset có danh sách `sources`, với `sync_status`: `CURRENT`, `NOT_STARTED`, `QUEUED`, `RUNNING`, `RETRY_WAIT`, `FAILED`, `SUPERSEDED` và các trạng thái job được lưu. Khi Silver thay đổi, Gold cũ không xuất hiện trong KPI hiện hành. Không dùng `0` thay thế số liệu chưa có; một KPI thực sự đếm được 0 vẫn trả `value: 0`.

Yêu cầu sửa là số liệu vận hành theo phòng được giao, độc lập với việc dataset đã có Gold hay chưa. Vì vậy có thể có yêu cầu sửa dù KPI đang rỗng.

FE nên gọi `/context` trước. Khi phòng theo role chưa được cấu hình hoặc ngừng hoạt động, endpoint này vẫn trả 200 với cờ cảnh báo; các endpoint dữ liệu trả `403 MANAGER_SCOPE_REQUIRED`. DB/migration chưa sẵn sàng trả `503 MANAGER_UNAVAILABLE`. Chi tiết lỗi kết nối không được trả về client.

Các chỉ số tỷ lệ trúng tuyển/nhập học, điểm trung bình, lương và so sánh theo kỳ chưa có công thức Gold trong bản hiện tại; không hiển thị giá trị giả cho các KPI này.

## Kiểm thử

```bash
.venv/bin/pytest tests/test_manager_api.py tests/test_gold_pipeline.py --no-cov
GOLD_LIVE_TEST=1 .venv/bin/pytest tests/test_gold_live.py::test_manager_reads_current_gold_with_dataset_ownership_and_pagination --no-cov
```

Live test dùng database và object kiểm thử riêng, kiểm tra số liệu Gold thật, scope theo chủ sở hữu, phân trang, Silver đổi phiên bản, danh sách/lịch sử yêu cầu sửa, tài khoản không gắn nhân viên/phân công và thu hồi role. Không ghi vào dữ liệu vận hành.

## Bản ghi nghiệp vụ cho từng phòng

Các API dưới đây đọc bảng nghiệp vụ PostgreSQL **đã được Gold đồng bộ**, cần schema `20261116_storage_model_v2.sql` (`make db-upgrade`). Không đọc lại file Silver hoặc MinIO khi manager gọi API. Không trả hồ sơ nhập thủ công chưa có bằng chứng Gold.

| API | Công dụng |
| --- | --- |
| `GET /{domain}/record-types` | Loại dữ liệu được phép xem, tên trường, PK, trường tìm kiếm và trạng thái hỗ trợ |
| `GET /{domain}/records/{entity}` | Danh sách có phân trang, tìm theo mã/tên, lọc dataset và trạng thái |
| `GET /{domain}/records/{entity}/{record_id}` | Chi tiết bằng ID thật trong DB; ngoài phạm vi, chưa SYNCED hoặc bản cũ trả 404 |

Ví dụ gọi với Bearer token của đúng manager:

```http
GET /api/v1/manager/hr/records/employee?search=NV0007&status=ACTIVE&limit=20&offset=0
GET /api/v1/manager/hr/records/assignment
GET /api/v1/manager/academic/records/course
GET /api/v1/manager/academic/records/student
GET /api/v1/manager/admissions/records/applicant
GET /api/v1/manager/admissions/records/admission
```

| Lĩnh vực / role / phòng | Các entity hỗ trợ |
| --- | --- |
| `hr` / `HR_MANAGER` / `PNS` | employee, department, position, assignment, lecturer, qualification |
| `academic` / `ACADEMIC_MANAGER` / `PDT` | student, course, class, class_lecturer, enrollment, enrollment_grade, semester, program, major, curriculum, curriculum_course |
| `admissions` / `ADMISSIONS_MANAGER` / `PTS` | applicant, admission, program, major, admission_year, admission_method |

Danh sách trả `{data_layer: "GOLD", domain, entity, items, total, limit, offset}`. Mỗi item có `record_id`, `values` (các trường trong allowlist) và `source`: `dataset_id`, `dataset_name`, `gold_job_id`, `silver_snapshot_id`, `published_at`, `synced_at`. Các ID nghiệp vụ là PK thật sau ánh xạ nguồn, không mặc định trùng mã ID trong CSV.

Bộ lọc: `dataset_id > 0`, `search` dài 1–100 ký tự, `status` dài 1–30, `limit` 1–100 (mặc định 20), `offset >= 0`. Tìm kiếm không phân biệt chữ hoa/thường; `%`, `_` được hiểu là nội dung nhập, không phải wildcard. Trạng thái không phân biệt hoa/thường. `record-types` chỉ rõ entity có trường tìm kiếm/trạng thái nào; gửi bộ lọc không được hỗ trợ trả 422. Không hỗ trợ lọc ngày, kỳ hoặc trường tùy ý trong API này.

Manager có role hiện hành xác định phòng tương ứng; không yêu cầu phân công nhân sự. Backend tiếp tục lọc dataset ACTIVE đúng domain, đúng owner. Mỗi hồ sơ phải có ánh xạ `gold_record_identity`, việc Gold SUCCEEDED đúng phiên bản hiện hành và `gold_sync_job.status = SYNCED`. Nếu cùng hồ sơ được cập nhật bởi nguồn khác, quyền đọc xét theo lần Gold ghi gần nhất trước khi lọc phòng; không lấy dữ liệu vừa bị ghi đè để gắn với bằng chứng nguồn cũ. Dataset chưa gán owner, Gold đang chờ/chặn/lỗi hoặc đã có phiên bản mới không trả dữ liệu chi tiết cũ. Các bản ghi chưa được đồng bộ không được suy ra thành giá trị 0 trong KPI.

Họ tên được trả cho manager trong phạm vi được phép; không trả email, số điện thoại, ngày sinh đầy đủ hoặc giấy tờ định danh qua API này. Không có quyền ghi hồ sơ/tài khoản/role bằng các endpoint manager.

`overview` có thêm `database_sync`: tổng số nguồn và các số đếm `synced`, `not_started`, `queued`, `running`, `blocked`, `failed`, `superseded`. `overview.status` vẫn thể hiện mức sẵn sàng **báo cáo tổng hợp Gold**; FE xem thêm `database_sync` để biết mức sẵn sàng của danh sách nghiệp vụ. Mỗi nguồn trong `datasets[].sources` có thêm `database_sync_status` và `database_sync_error`. Ví dụ Gold đã CURRENT nhưng DB BLOCKED vì thiếu position_id thì biểu đồ vẫn có thể xem, còn phân công chi tiết chưa được trả.

Kiểm thử nghiệp vụ cả ba phòng trên database cô lập:

```bash
AUTO_PIPELINE_LIVE_TEST=1 .venv/bin/pytest tests/test_manager_records_live.py --no-cov
```

`needs_department_assignment` được giữ để tương thích client cũ và luôn `false` cho manager. FE dùng `departments` và `needs_department_configuration` để báo danh mục phòng chưa sẵn sàng, không yêu cầu quản trị tạo hồ sơ nhân viên/phân công để mở quyền manager.
