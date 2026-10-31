# Bronze storage

## Phân tích và lựa chọn

Dự án đã có MinIO, volume `minio_data`, bucket Bronze/Silver/Gold và reader đọc
`s3://bucket/key`. Dùng MinIO làm storage chính; local dành cho phát triển khi
Docker không chạy. Không cần bổ sung HDFS hoặc dịch vụ lưu trữ khác.

Phần cần bổ sung: quy ước partition dùng chung, lệnh khởi tạo/upload, thứ tự
khởi động backend và kiểm thử. Compose hiện chờ `minio-init` thành công trước
khi chạy backend. Init có thể chạy lại trên volume cũ, không xóa dữ liệu.

## Cấu trúc

```text
bronze/                         # bucket hoặc local root
  admissions/YYYY/MM/DD/<batch_id>/applicant.csv
  hr/YYYY/MM/DD/<batch_id>/employee.csv
```

- Ngày là ngày tiếp nhận dữ liệu, mặc định theo `Asia/Ho_Chi_Minh`; dùng `--date`
  cho backfill. Không dùng ngày nghiệp vụ trong từng record để phân vùng raw file.
- Source là slug chữ thường, số, `_`, `-`; ví dụ admissions, hr.
- Batch mặc định là UUID mới mỗi lần upload; tên file giữ nguyên cho reader đối
  chiếu `source_table`. Nội dung file được giữ nguyên, không chuẩn hóa ở bước này.
- MinIO dùng object prefix, không có thư mục vật lý. Prefix xuất hiện khi upload
  object đầu tiên; init không tạo file giả có thể bị reader hiểu nhầm là dữ liệu.
- Versioning bảo toàn phiên bản khi cùng key được ghi lại. Lưu cả `uri` và
  `version_id` trả về để đọc đúng phiên bản; versioning không phải WORM hay backup.
- Local từ chối ghi đè key đã tồn tại. `data/bronze/` đã được gitignore.

## MinIO

Từ thư mục gốc, cấu hình `.env` theo `.env.example`, sau đó:

```powershell
docker compose up -d minio
docker compose run --rm minio-init
```

API: `localhost:9000`; console: http://localhost:9001. Thông tin đăng nhập từ
`MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`; bucket từ `MINIO_BUCKET_BRONZE`.
Volume `minio_data` giữ dữ liệu qua restart; không dùng `docker compose down -v`
nếu cần giữ dữ liệu. Không cấu hình tự động hết hạn dữ liệu Bronze.

Từ thư mục `backend`, dùng Python trong virtualenv của dự án:

```powershell
..\.venv\Scripts\python.exe -m app.infra.minio.bronze init
..\.venv\Scripts\python.exe -m app.infra.minio.bronze upload --source admissions --file ..\data\samples\admissions\applicant.csv
```

Thay đường dẫn file ví dụ bằng file thực tế. Endpoint khi chạy trên host là
`localhost:9000`, trong Compose là `minio:9000`. Init CLI chỉ quản lý Bronze;
Compose init tiếp tục quản lý cả ba bucket. Bucket mới mặc định private; công cụ
không sửa policy của bucket đã có. Tài khoản root hiện tại phù hợp môi trường dev;
triển khai production cần tài khoản ứng dụng giới hạn quyền và backup riêng.

## Local

Từ `backend`:

```powershell
..\.venv\Scripts\python.exe -m app.infra.minio.bronze init --backend local
..\.venv\Scripts\python.exe -m app.infra.minio.bronze upload --backend local --source hr --file C:\imports\employee.csv
```

Root mặc định `../data/bronze` tính từ working directory; dùng `--root` để chỉ định
đường dẫn khác. Init tạo partition ngày hiện tại cho admissions và hr. Dùng
`--sources admissions hr finance --date 2026-10-15` để chọn nguồn/ngày khác.
Upload tự tạo partition cho nguồn/ngày được chọn. Với reader local, dùng đường
dẫn filesystem của file được tạo; với MinIO dùng URI S3 và version ID.

Phạm vi công cụ này là storage và upload thủ công. Để thực thi ingestion từ
catalog bằng worker, xem [Ingestion Execution Service](ingestion-execution.md).
Chưa có bộ lập lịch thu thập dữ liệu định kỳ.
