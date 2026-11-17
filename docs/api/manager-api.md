# Manager MVP – API và UI

Manager **xem tình hình → phát hiện bất thường → tìm hiểu → theo dõi xử lý → xem báo cáo cập nhật**. Manager không giao việc, sửa dữ liệu, xác minh lỗi hay điều khiển pipeline.

## Phạm vi truy cập

Ba role dùng chung workspace, với các cặp domain/phòng: `HR_MANAGER → hr/PNS`, `ACADEMIC_MANAGER → academic/PDT`, `ADMISSIONS_MANAGER → admissions/PTS`.

Quyền hiện hành được đối chiếu DB. Dataset phải ACTIVE, đúng domain và phòng sở hữu; không tự mở quyền toàn domain. Dataset chưa gán owner không hiển thị. Người có nhiều role chỉ được hợp các cặp domain/phòng tương ứng. Phân quyền theo phòng sở hữu dataset, chưa phải lọc từng bản ghi theo phòng công tác.

Các chính sách xem toàn domain, trường nhạy cảm và truy vấn chi tiết chưa được mở rộng trong đợt thu gọn này. ADMIN/DATA_ADMIN/STAFF/VIEWER không tự có quyền vào API Manager.

## API giữ lại

Tiền tố `/api/v1`; tất cả yêu cầu Bearer token.

| API | UI sử dụng |
| --- | --- |
| `GET /manager/context` | Kiểm tra phòng/domain có quyền |
| `GET /manager/overview` | KPI, mức sẵn sàng báo cáo, thời điểm cập nhật và chất lượng chung |
| `GET /manager/analytics` | Biểu đồ cố định trong Tổng quan, không cần menu Phân tích riêng |
| `GET /manager/issues` | Danh sách yêu cầu sửa thuộc phòng/domain |
| `GET /manager/issues/stats` | Thống kê và phân trang yêu cầu |
| `GET /manager/issues/{issue_id}` | Chi tiết, người phụ trách, thời điểm cập nhật và lịch sử |
| `POST /nlq/ask` | Hỏi AI; hiện trả `503 NLQ_UNAVAILABLE` vì chưa có bộ thực thi thật có kiểm soát phạm vi |
| `GET /pipeline/profiles/review-queue` | Grant còn hiệu lực và cấu hình đã đăng ký đúng domain được duyệt |
| `GET /pipeline/profiles/{id}/revisions` | Lịch sử revision theo quyền governance hiện có |
| `POST /pipeline/profiles/{id}/approve` | Duyệt revision/fingerprint chính xác với grant hợp lệ và căn cứ nghiệp vụ |
| `POST /pipeline/profiles/{id}/revisions/{revision}/revoke` | Thu hồi quyết định theo quyền governance hiện có |

API dùng chung cho DATA_ADMIN/ADMIN/STAFF được giữ nguyên. Các API cấp/thu hồi grant tiếp tục chỉ dành cho ADMIN. Phê duyệt cấu hình không đồng nghĩa phê duyệt từng file upload hoặc từng snapshot.

## API và code đã bỏ

- `/manager/datasets`, `/manager/datasets/{id}` và các schema truy xuất nguồn kỹ thuật riêng của Manager.
- `/manager/{domain}/record-types`, `/manager/{domain}/records/{entity}`, `/manager/{domain}/records/{entity}/{id}` và repository đọc hồ sơ chi tiết.
- Trường `database_sync` trong tổng quan Manager: thuộc việc đồng bộ bảng nghiệp vụ chi tiết đã nằm ngoài MVP.
- Đăng ký router rỗng `/dashboard` và `/query-history`; chúng chưa từng cung cấp endpoint.

Các đường dẫn API đã bỏ trả 404. FE chuyển các URL trang cũ `/manager/analytics`, `/manager/datasets`, `/manager/records` về Tổng quan, `/manager/history` về tab lịch sử của Hỏi AI.

## Bộ lọc và dữ liệu báo cáo

`overview`: `domain`, `dataset_id`, `limit` 1–100 (mặc định 20), `offset` >= 0. Phân trang chỉ tác động `kpis`; số dataset/nguồn, chất lượng và thời điểm cập nhật tính toàn phạm vi.

`analytics`: thêm `entity`, `metric` (mặc định `record_count`). Các biểu đồ UI dùng:

| Domain | Entity | Metric |
| --- | --- | --- |
| hr | employee | record_count_by_employee_status |
| academic | student | record_count_by_program_id |
| admissions | admission | record_count_by_admission_status |

KPI/biểu đồ đọc `gold.current_metrics`. Mỗi nguồn giữ riêng dataset, scope, entity, revision/snapshot và `finished_at`. Đơn vị là **bản ghi**, không tự cộng các nguồn thành số người duy nhất. Không có dữ liệu trả danh sách rỗng, không tạo KPI bằng 0. Chưa hỗ trợ công thức tỷ lệ, so sánh theo kỳ hoặc khoảng ngày nghiệp vụ; bộ lọc không hỗ trợ trả 422.

`overview.status`: `NO_DATA` (chưa có nguồn), `PENDING` (chưa có Gold hiện hành), `PARTIAL` (một phần nguồn đã sẵn sàng), `READY` (mọi nguồn đã có Gold hiện hành). `last_updated_at` là lần công bố Gold mới nhất; từng KPI vẫn có thời gian riêng. Không dùng thời gian này để khẳng định mọi KPI cập nhật cùng lúc.

`issues`/`issues/stats`: `domain`, `dataset_id`, `department_id`, `assignee_user_id`, `status`, `mine_only`, `limit`, `offset`. Các filter không mở rộng scope. `issues` trả danh sách, `stats` không chịu phân trang. Chi tiết ngoài phạm vi trả 404.

Manager chỉ xem trạng thái `ASSIGNED`, `PENDING_VERIFICATION`, `VERIFIED`. **VERIFIED là kết quả sửa đã xác minh, chưa khẳng định báo cáo Gold đã được cập nhật.** Chưa có liên kết lỗi → KPI nên UI chỉ hiển thị chất lượng chung và thời gian công bố, không gán lỗi cụ thể cho KPI.

`context` vẫn trả cờ `needs_department_configuration`; phòng chưa cấu hình trả 403 ở API dữ liệu. `needs_department_assignment` giữ tương thích và luôn false. DB chưa sẵn sàng trả 503, không lộ thông tin kết nối.

## Hỏi AI và lịch sử

Không sử dụng MockLLM/EmptyGoldQueryExecutor làm kết quả thật. API Hỏi AI yêu cầu role Manager và trả 503 cho tới khi có tích hợp truy vấn thật kiểm soát domain, dataset, trường nhạy cảm và quyền hiện hành. UI hiển thị thông báo lỗi từ API, không ghi nhận đó là kết quả “không có dữ liệu”.

Lịch sử hiện lưu cục bộ theo tài khoản trên trình duyệt, chưa có API hoặc đồng bộ đa thiết bị. Không chuyển lịch sử dùng chung cũ sang tài khoản bất kỳ. Sidebar chỉ gồm Tổng quan, Hỏi UniLake AI, Chất lượng dữ liệu; Phê duyệt nguồn chỉ hiện khi có grant hợp lệ. Tài khoản/đăng xuất dùng menu tài khoản hiện có.

## Kiểm thử

```bash
.venv/bin/pytest tests/test_manager_api.py tests/test_silver_profile_governance.py tests/test_architecture.py tests/test_gold_pipeline.py --no-cov
GOLD_LIVE_TEST=1 .venv/bin/pytest tests/test_gold_live.py::test_manager_reads_current_gold_with_dataset_ownership_and_pagination --no-cov
```

Live test chỉ chạy khi bật rõ ràng, dùng dữ liệu kiểm thử riêng.
