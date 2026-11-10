# Quản lý Bronze → Silver trên Swagger

Các API dùng tiền tố `/api/v1`. Sau khi Render triển khai code mới, mở `/docs` của service và đăng nhập qua `auth/login`. Dùng nút **Authorize** để nhập access token của ADMIN hoặc DATA_ADMIN.

## Các nhóm API

| Nhóm trên Swagger | API | Mục đích |
| --- | --- | --- |
| datasets | `GET /source-systems` | Chọn hệ thống nguồn đang hoạt động |
| datasets | `GET /datasets` | Chọn bộ dữ liệu Bronze để tải file; STAFF chỉ thấy miền được cấp quyền |
| datasets | `POST /datasets` | Quản trị viên đăng ký bộ dữ liệu Bronze |
| silver-profiles | `GET /pipeline/profiles` | Liệt kê cấu hình nguồn |
| silver-profiles | `GET /pipeline/profiles/{profile_id}` | Xem đầy đủ quy tắc và số lần cập nhật |
| silver-profiles | `POST /pipeline/profiles` | Tạo cấu hình trong PostgreSQL |
| silver-profiles | `PUT /pipeline/profiles/{profile_id}` | Sửa cấu hình với `expected_revision` |
| silver-preview | `POST /pipeline/cleaning/ingestion-jobs/{ingestion_job_id}/preview` | Xem trước làm sạch và chuẩn hóa, không ghi Silver |
| pipeline | `POST /pipeline/cleaning/ingestion-jobs/{ingestion_job_id}` | Làm sạch, chuẩn hóa và công bố Silver |
| silver-snapshots | `GET /pipeline/silver/snapshots` | Liệt kê phiên bản theo profile |
| silver-snapshots | `GET /pipeline/silver/snapshots/active` | Xem phiên bản hiện hành |
| silver-snapshots | `GET /pipeline/silver/snapshots/{snapshot_id}` | Xem bản kê và nguồn gốc phiên bản |
| silver-snapshots | `GET /pipeline/silver/snapshots/{snapshot_id}/data` | Xem dữ liệu theo `entity`, `limit`, `offset` |
| silver-snapshots | `POST /pipeline/silver/snapshots/{snapshot_id}/activate` | Chọn phiên bản đã công bố làm phiên bản hiện hành |

API quản lý profile, preview và snapshot dành cho ADMIN/DATA_ADMIN. STAFF chỉ được chọn bộ dữ liệu Bronze trong miền tương ứng và tải file qua Upload API.

## Thử theo thứ tự

1. `GET /source-systems`, chọn mã hệ thống nguồn có sẵn.
2. `POST /datasets`, gửi `dataset_code`, `dataset_name`, `domain`, `source_system_id`. Hệ thống chọn tầng Bronze; không nhận đường dẫn ghi tùy ý.
3. `POST /ingestion/uploads`, chọn `dataset_id`, tải file và chờ job `SUCCESS`.
4. `POST /pipeline/profiles`, gửi `profile_id` và `definition`. Swagger hiển thị toàn bộ cấu trúc `SourceProfile` trong phần Schemas. Điền mã nguồn do upload tạo, mã dataset, tên file, cấu trúc nguồn/đích, ánh xạ cột, quy tắc giá trị và phiên bản đối chiếu mã đã đăng ký. Xem thêm [cấu hình nguồn thật](standardization/source-silver-flow.md).
5. Gọi preview với body `{"profile_id":"ma-profile-cua-ban"}`. API kiểm tra toàn bộ file nhưng trả tối đa 100 dòng mẫu. Không ghi lịch sử cleaning, không lưu quarantine và không đổi dữ liệu Silver. Phần `publication_checked=false` nhắc rằng liên kết với toàn bộ snapshot còn được kiểm tra khi công bố.
6. Gọi API Cleaning với `{"profile_id":"ma-profile-cua-ban","base_snapshot":"active"}`.
7. Dùng nhóm `silver-snapshots` để xem phiên bản và dữ liệu. Các API này cần query `profile_id`; API dữ liệu cần thêm `entity`.

Profile tạo/sửa qua API nằm trong PostgreSQL, được Cleaning và Preview đọc trực tiếp. Việc khởi động lại service không làm mất profile. Profile khai báo sẵn trong file vẫn đọc được; bản PostgreSQL có ưu tiên cao hơn. Khi sửa, gửi đúng `expected_revision` từ lần GET gần nhất để tránh ghi đè thay đổi của người khác.

Khi chọn phiên bản hiện hành, gửi `expected_current_snapshot` lấy từ API `/active`. Backend đọc lại bản kê và Parquet, kiểm tra checksum, cấu trúc và liên kết theo cấu hình hiện tại trước khi đổi. Nếu phiên bản hiện hành vừa thay đổi, trả `409`; thao tác thành công được ghi vào `app.silver_activation`. Đổi cấu hình không tương thích có thể khiến snapshot cũ không được chấp nhận.

Giới hạn số dòng trả về không đồng nghĩa đọc Parquet theo từng trang: API xem dữ liệu hiện tải snapshot vào bộ nhớ rồi cắt trang. Dùng cho quản trị và kiểm tra dữ liệu; cần tối ưu riêng khi snapshot lớn.

## Triển khai trên Render

Code phải được đưa vào nhánh service đang theo dõi (repository hiện cấu hình `develop`), CI đạt và Render triển khai bản mới. Swagger được tạo trực tiếp từ code ứng dụng; không cần nhập file OpenAPI vào Render.

Trên database **của Render**, áp dụng migration mới sau các migration Silver đã có:

```bash
psql -v ON_ERROR_STOP=1 -f infra/postgres/migrations/20261110_silver_management.sql
```

Lệnh dùng các biến `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGSSLMODE` của database đích, theo [hướng dẫn Render](render.md). Migration chạy trên PostgreSQL local không cập nhật database Render.

Chỉ xem Swagger không cần MinIO. Để upload và chạy pipeline thực tế, service cần PostgreSQL, MinIO truy cập được từ Render và tài khoản dịch vụ tương ứng; ingestion worker cần chạy riêng. Blueprint hiện chỉ khai báo web service và database, không tự khởi chạy worker hay MinIO.

File OpenAPI lưu sẵn được cập nhật bằng:

```bash
python -m scripts.export_openapi
python -m scripts.export_openapi --check
```
