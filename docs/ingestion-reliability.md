# Theo dõi và chạy lại Ingestion

Ngày cập nhật: 10/11/2026.

## Phạm vi đợt 1

Đợt này giữ nguyên hàng đợi PostgreSQL. Worker tự gửi dấu hiệu hoạt động kể cả khi không có dữ liệu cần xử lý; Docker đánh dấu worker không khỏe nếu dấu hiệu đó quá cũ.

Ba API mới:

| API | Ai dùng | Mục đích |
| --- | --- | --- |
| `GET /api/v1/ingestion/jobs/stats` | ADMIN, DATA_ADMIN, STAFF | Đếm job theo trạng thái trong phạm vi được xem |
| `POST /api/v1/ingestion/jobs/{job_id}/retry` | ADMIN, DATA_ADMIN | Tạo một lần chạy lại từ file đã tải lên |
| `GET /api/v1/ingestion/jobs/{job_id}/attempts` | ADMIN, DATA_ADMIN, STAFF | Xem các lần chạy của cùng một chuỗi |

`VIEWER` không được xem job, thống kê hoặc lịch sử ingestion. STAFF chỉ xem dữ liệu thuộc miền phòng ban của mình.

## Lịch sử chạy lại

Mỗi lần chạy lại là một job mới. Job gốc và các job chạy lại dùng chung `root_job_id`; `attempt_number` bắt đầu từ 1. Job cũ không bị thay đổi nên lịch sử kiểm tra vẫn đầy đủ.

Ví dụ: job 105 thất bại, chạy lại tạo job 106. Cả hai có `root_job_id=105`; lần chạy lần lượt là 1 và 2.

MVP chỉ cho chạy lại job `UPLOAD_V1`, vì file này có sẵn phiên bản MinIO và mã kiểm tra nội dung đã ghim. Job đọc nguồn đã đăng ký chưa có cơ chế giữ cố định đầu vào đầy đủ nên phải tạo job mới thay vì chạy lại.

Chỉ job `FAILED` mới được chạy lại. Hệ thống cũng từ chối nếu chuỗi đang có job `QUEUED`/`RUNNING` hoặc đã phát hiện Bronze có object cần đối soát. Điều này tránh tạo thêm dữ liệu Bronze trùng khi worker dừng sau lúc ghi object nhưng trước lúc cập nhật trạng thái cuối cùng.

## Worker heartbeat và Docker healthcheck

Worker ghi thời điểm hoạt động vào bảng `app.ingestion_worker_heartbeat` mỗi 10 giây mặc định, kể cả lúc rảnh. Biến cấu hình:

| Biến | Mặc định | Ý nghĩa |
| --- | ---: | --- |
| `INGESTION_WORKER_ID` | `ingestion-worker` trong Docker | Tên phân biệt worker |
| `WORKER_HEARTBEAT_SECONDS` | 10 | Chu kỳ ghi dấu hiệu hoạt động |
| `WORKER_HEALTH_STALE_SECONDS` | 45 | Thời gian tối đa Docker chấp nhận dấu hiệu cũ |

Docker healthcheck kiểm tra file dấu hiệu được worker cập nhật sau khi ghi heartbeat vào PostgreSQL thành công. Nếu PostgreSQL không truy cập được, file không được làm mới và Docker đánh dấu container không khỏe.

## Đợt 2: tự phục hồi và theo dõi

Worker tự tạo một job mới ở trạng thái `RETRY_WAIT` cho lỗi hạ tầng tạm thời như MinIO, mạng, timeout hoặc PostgreSQL. Lần đầu chờ 10 giây, lần tiếp theo chờ 30 giây; mặc định tối đa ba lần chạy tính cả lần đầu. Lỗi file, validation, checksum và cấu hình không tự chạy lại.

`GET /api/v1/ingestion/jobs/metrics` trả thời gian chờ trung bình, thời gian xử lý trung bình, tỷ lệ thất bại và tuổi của job chờ lâu nhất. `GET /api/v1/ingestion/workers` dành cho ADMIN và DATA_ADMIN, hiển thị worker còn hoạt động, lần heartbeat gần nhất và job đang chạy.

Nếu một worker đã ghi và xác minh Bronze nhưng bị dừng trước khi chốt `SUCCESS`, bản ghi checkpoint `bronze_output` được giữ lại. Lần retry đọc lại đúng object/version/checksum đó và chốt thành công, thay vì ghi thêm object Bronze.

Worker dọn file ở kho tiếp nhận sau khi toàn bộ chuỗi retry đã kết thúc và quá thời hạn giữ mặc định 168 giờ. Tác vụ dọn dùng tài khoản MinIO `UPLOAD_CLEANUP` riêng, chỉ có quyền xóa object trong `incoming/*`; sau khi cập nhật phải chạy lại `upload_accounts export-runtime` và `apply` để tạo tài khoản này.

Trước khi chạy API/worker mới trên database có sẵn, áp dụng migration `infra/postgres/migrations/20261110_ingestion_reliability_mvp.sql`.
