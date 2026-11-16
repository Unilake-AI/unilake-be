# Cleaning Run Management — Nhóm A

| API | Hành vi |
| --- | --- |
| `GET /api/v1/cleaning-runs` | Lọc theo `dataset_id`, `ingestion_job_id`, `cleaning_status`, `publication_status`, `started_from`, `started_to`. Dùng `limit` 1–100, mặc định 20, và `offset` mặc định 0. Khoảng thời gian có múi giờ, gồm mốc đầu và loại mốc cuối. |
| `GET /api/v1/cleaning-runs/{run_id}` | Trả chi tiết theo UUID của lượt. Gồm thống kê, trạng thái, phiên bản Bronze, thời gian và snapshot nếu đã xuất bản. |
| `GET /api/v1/cleaning-runs/{run_id}/report` | Kiểm tra quyền, đọc và xác minh JSON MinIO theo tham chiếu của lượt. Trả `404 CLEANING_REPORT_NOT_FOUND` khi chưa có báo cáo, `503 CLEANING_REPORT_UNAVAILABLE` khi có tham chiếu nhưng báo cáo không đọc hoặc xác minh được. |

Cả ba API yêu cầu `ADMIN` hoặc `DATA_ADMIN`. API danh sách và chi tiết chỉ đọc PostgreSQL. `report_available` cho biết PostgreSQL đã lưu tham chiếu báo cáo, API không kiểm tra MinIO cho từng lượt. Mã lượt không tồn tại trả `404 CLEANING_RUN_NOT_FOUND`.

## Kết quả cuối cùng và tải báo cáo — cập nhật 16/11/2026

API `/report` xác minh file kiểm tra trên MinIO, sau đó bổ sung trạng thái xuất
bản, snapshot, số dòng và thời gian cuối cùng từ lịch sử PostgreSQL. File kiểm
tra được tạo trước lúc công bố có thể mang trạng thái `CLEANED`; API không dùng
trạng thái cũ này để kết luận lượt đã hoặc chưa xuất bản. File và checksum gốc
được giữ nguyên để các bằng chứng đã công bố vẫn xác minh được.

Phản hồi gồm `cleaning_status`, `publication_status`, `publication`,
`records_published`, `silver_snapshot_id` và `issue_summary` với ba nhóm:

- `error`: lỗi hoặc dòng bị cách ly.
- `warning`: cảnh báo cần xem lại.
- `information`: ghi nhận xử lý hợp lệ, ví dụ `NULL_VALUE_PRESERVED` mức `INFO`.

Số thông báo không phải số dòng lỗi. Một ô trống được phép theo cấu hình có thể
được ghi nhận mức thông tin trong khi dòng vẫn hợp lệ và được lưu Silver. Nếu
cột bắt buộc có giá trị, phải xác nhận quy định bắt buộc cho cột đó.

FE dùng trạng thái chính `result.status` của POST làm kết luận xử lý; phản hồi cũ
có thể chưa có `publication.status`. Các phản hồi POST mới bổ sung cả trường này
để thống nhất cách đọc. Nhóm ghi nhận hợp lệ được hiển thị riêng, không dùng nhãn
“Vấn đề phát hiện” chung cho mọi thông báo.

Trên chi tiết lượt chạy, **Xem báo cáo chi tiết** mở nội dung trên trang;
**Tải CSV (Excel)** và **Tải JSON** tạo file tải xuống thực tế từ API `/report`.
Mỗi lần tải đọc lại báo cáo để lấy trạng thái cuối cùng. CSV có dấu UTF-8 để đọc
tiếng Việt, phần thống kê và toàn bộ thông báo; nội dung nguồn được xuất dạng
chữ, tránh bị hiểu thành công thức. Các lượt chạy cũ cũng sử dụng được cách đọc
và tải này, không cần chạy lại Cleaning hoặc sửa dữ liệu Silver.

1. POST chạy cleaning xác minh job Bronze và tạo lượt `RUNNING`, `NOT_STARTED` trước khi đọc file. Job không tồn tại hoặc không đủ điều kiện không tạo lượt. Mỗi lần chạy tạo UUID mới, kể cả khi dùng lại cùng một job.
2. Sau cleaning, lưu số liệu và chuyển `cleaning_status` sang `COMPLETED`. Nếu có dòng cách ly, lưu tham chiếu sau khi báo cáo MinIO được xác minh. Lượt bị chặn trả `BLOCKED` ở lịch sử và `QUARANTINED` ở phản hồi POST. Lượt trống trả `SKIPPED` ở lịch sử.
3. Khi Silver xuất bản snapshot mới, ghi kết quả lịch sử trong cùng giao dịch cập nhật snapshot. Khi dùng lại snapshot đã xuất bản, lịch sử trỏ tới snapshot đó. `records_published` chỉ đếm các dòng từ lượt hiện tại. POST thành công hoặc bị chặn trả `run_id` để mở trang chi tiết.
4. Lỗi trong bước cleaning ghi `FAILED` ở cleaning. Lỗi sau khi cleaning hoàn tất ghi `FAILED` ở publication nếu PostgreSQL vẫn ghi được. Nếu tiến trình bị dừng đột ngột, lịch sử có thể còn ở trạng thái đang xử lý. Chưa có tác vụ đối soát tự động cho trường hợp này.

Database mới có bảng trong DDL khởi tạo. Database hiện có cần áp dụng `infra/postgres/migrations/20261106_cleaning_runs.sql` trước khi dùng API. Migration đã chạy trên Docker dev. Lịch sử bắt đầu từ những lượt chạy với code mới, chưa nhập lại các báo cáo cũ.
