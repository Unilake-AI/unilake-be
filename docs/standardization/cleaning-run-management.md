# Cleaning Run Management — Nhóm A

| API | Hành vi |
| --- | --- |
| `GET /api/v1/cleaning-runs` | Lọc theo `dataset_id`, `ingestion_job_id`, `cleaning_status`, `publication_status`, `started_from`, `started_to`. Dùng `limit` 1–100, mặc định 20, và `offset` mặc định 0. Khoảng thời gian có múi giờ, gồm mốc đầu và loại mốc cuối. |
| `GET /api/v1/cleaning-runs/{run_id}` | Trả chi tiết theo UUID của lượt. Gồm thống kê, trạng thái, phiên bản Bronze, thời gian và snapshot nếu đã xuất bản. |
| `GET /api/v1/cleaning-runs/{run_id}/report` | Kiểm tra quyền, đọc và xác minh JSON MinIO theo tham chiếu của lượt. Trả `404 CLEANING_REPORT_NOT_FOUND` khi chưa có báo cáo, `503 CLEANING_REPORT_UNAVAILABLE` khi có tham chiếu nhưng báo cáo không đọc hoặc xác minh được. |

Cả ba API yêu cầu `ADMIN` hoặc `DATA_ADMIN`. API danh sách và chi tiết chỉ đọc PostgreSQL. `report_available` cho biết PostgreSQL đã lưu tham chiếu báo cáo, API không kiểm tra MinIO cho từng lượt. Mã lượt không tồn tại trả `404 CLEANING_RUN_NOT_FOUND`.

1. POST chạy cleaning xác minh job Bronze và tạo lượt `RUNNING`, `NOT_STARTED` trước khi đọc file. Job không tồn tại hoặc không đủ điều kiện không tạo lượt. Mỗi lần chạy tạo UUID mới, kể cả khi dùng lại cùng một job.
2. Sau cleaning, lưu số liệu và chuyển `cleaning_status` sang `COMPLETED`. Nếu có dòng cách ly, lưu tham chiếu sau khi báo cáo MinIO được xác minh. Lượt bị chặn trả `BLOCKED` ở lịch sử và `QUARANTINED` ở phản hồi POST. Lượt trống trả `SKIPPED` ở lịch sử.
3. Khi Silver xuất bản snapshot mới, ghi kết quả lịch sử trong cùng giao dịch cập nhật snapshot. Khi dùng lại snapshot đã xuất bản, lịch sử trỏ tới snapshot đó. `records_published` chỉ đếm các dòng từ lượt hiện tại. POST thành công hoặc bị chặn trả `run_id` để mở trang chi tiết.
4. Lỗi trong bước cleaning ghi `FAILED` ở cleaning. Lỗi sau khi cleaning hoàn tất ghi `FAILED` ở publication nếu PostgreSQL vẫn ghi được. Nếu tiến trình bị dừng đột ngột, lịch sử có thể còn ở trạng thái đang xử lý. Chưa có tác vụ đối soát tự động cho trường hợp này.

Database mới có bảng trong DDL khởi tạo. Database hiện có cần áp dụng `infra/postgres/migrations/20261106_cleaning_runs.sql` trước khi dùng API. Migration đã chạy trên Docker dev. Lịch sử bắt đầu từ những lượt chạy với code mới, chưa nhập lại các báo cáo cũ.
