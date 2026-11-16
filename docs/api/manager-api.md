# API màn hình Manager

Tiền tố `/api/v1/manager`, xác thực Bearer token hiện tại. Ba role được hỗ trợ: `HR_MANAGER`, `ACADEMIC_MANAGER`, `ADMISSIONS_MANAGER`. Các API chỉ đọc; không yêu cầu thêm migration ngoài cấu trúc Gold và danh mục phòng ban hiện có.

## Phạm vi truy cập

Backend đối chiếu role hiện hành trong PostgreSQL với token và phân công còn hiệu lực:

| Role | Lĩnh vực | Mã phòng phụ trách |
| --- | --- | --- |
| HR_MANAGER | hr | PNS |
| ACADEMIC_MANAGER | academic | PDT |
| ADMISSIONS_MANAGER | admissions | PTS |

Tài khoản, nhân viên, phân công và phòng ban phải còn hoạt động; ngày hiện tại theo `Asia/Ho_Chi_Minh` phải nằm trong khoảng phân công. Tài khoản nhiều role được hợp các cặp lĩnh vực/phòng hợp lệ, không ghép chéo lĩnh vực của role này với phòng của role khác. Phân công kiêm nhiệm đang hiệu lực cũng được xét.

Dataset phải đang `ACTIVE`, có `domain` phù hợp và `owner_department_id` trùng phòng phụ trách được phân công. Dataset chưa gán phòng sở hữu không được hiển thị. Quyền này giới hạn theo **phòng sở hữu dataset**, không phải lọc từng người/bản ghi trong dataset theo phòng công tác. Gold hiện chưa có tổng hợp đa chiều để thực hiện kiểu lọc bản ghi đó.

`ADMIN`, `DATA_ADMIN`, `VIEWER`, STAFF không tự có quyền vào màn hình manager. Nếu có thêm role manager, các API này vẫn dùng phạm vi manager. API `/pipeline/gold/*` cũng kiểm tra quyền sở hữu/phân công đối với lĩnh vực của manager để không đi vòng qua đường đọc Gold cũ; ADMIN/DATA_ADMIN/VIEWER vẫn giữ quyền tổng hợp vốn có. Không thêm quyền ghi hay duyệt công bố cho manager.

## Endpoint

| Method và đường dẫn | Kết quả |
| --- | --- |
| `GET /context` | Role, lĩnh vực, phòng hợp lệ, `default_domain`, `needs_department_assignment` |
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

FE nên gọi `/context` trước. Khi thiếu phân công, endpoint này vẫn trả 200 với cờ cảnh báo; các endpoint dữ liệu trả `403 MANAGER_SCOPE_REQUIRED`. DB/migration chưa sẵn sàng trả `503 MANAGER_UNAVAILABLE`. Chi tiết lỗi kết nối không được trả về client.

Các chỉ số tỷ lệ trúng tuyển/nhập học, điểm trung bình, lương và so sánh theo kỳ chưa có công thức Gold trong bản hiện tại; không hiển thị giá trị giả cho các KPI này.

## Kiểm thử

```bash
.venv/bin/pytest tests/test_manager_api.py tests/test_gold_pipeline.py --no-cov
GOLD_LIVE_TEST=1 .venv/bin/pytest tests/test_gold_live.py::test_manager_reads_current_gold_with_dataset_ownership_and_pagination --no-cov
```

Live test dùng database và object kiểm thử riêng, kiểm tra số liệu Gold thật, scope theo chủ sở hữu, phân trang, Silver đổi phiên bản, danh sách/lịch sử yêu cầu sửa và phân công hết hiệu lực. Không ghi vào dữ liệu vận hành.
