# Định danh dịch vụ Bronze (UDL-142)

Ba tài khoản là Service Account MinIO (child credentials), không phải
IAM Role AWS hoặc role PostgreSQL. Script provisioning dùng tài khoản quản trị
từ cấu hình hiện có để tạo SA với inline policy giới hạn trong bucket Bronze.

| Service Account | Thành phần mục tiêu | Quyền object |
| --- | --- | --- |
| bronze-ingestion-writer | Bước upload của ingestion-worker | Put, multipart; không Get/Delete |
| bronze-verifier-reader | Bước xác minh metadata/checksum sau upload | Get/GetObjectVersion; không Put/Delete |
| bronze-pipeline-reader | Pipeline đọc Bronze sang Silver | Get/GetObjectVersion và list; không Put/Delete |

Đã tạo trên MinIO local ngày 18/10/2026; Admin API xác nhận cả ba có trạng thái
`on`. Inline policy đọc lại khớp các action/resource thiết kế (MinIO có thể đổi
thứ tự action khi serialize). Access key tương ứng là `bronze-writer`,
`bronze-verifier`, `bronze-reader`; đây là định danh, không phải secret.

Writer được đọc cấu hình bucket/versioning, không được đọc nội dung object.
Policy writer có Deny tường minh cho GetObject, GetObjectVersion, DeleteObject
và DeleteObjectVersion trong Bronze. Chỉ cấp PutObject và thao tác multipart cần
thiết; S3 không có thao tác append trực tiếp cho object thông thường. Dữ liệu mới
được upload thành object/version mới theo quy ước Bronze.
Verifier và reader không có quyền admin hoặc ghi Silver. Các quyền không xuất
hiện trong policy không được cấp cho SA. Runtime đã tách client upload và xác minh;
backend/worker không được nạp credentials root. Thiếu credentials SA sẽ báo lỗi,
không fallback sang admin. Client quản trị trong `bronze.py` chỉ còn dùng ở CLI
và fixture quản trị của live tests.

Worker có cả SA writer và verifier trong cùng process nên tổng quyền của process
vẫn gồm đọc để xác minh và ghi. Đây là tách quyền theo client; nếu yêu cầu service
ingestion hoàn toàn không giữ quyền đọc thì cần tách verifier sang service riêng.

Từ repo root:

```bash
PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts plan
PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts prepare
PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts apply
```

Với SA writer đã có, cập nhật policy mà không đổi secret hoặc quyền SA chỉ đọc:

```bash
PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts update-writer
```

Lệnh đọc lại inline policy để xác minh, bỏ qua khác biệt thứ tự action/resource.

`prepare` tạo `.env.bronze-accounts.local` với quyền 600 và secret ngẫu nhiên,
không gọi MinIO. File được bỏ qua bởi quy tắc `.env*.local` sẵn có. Không commit,
in hoặc gửi nội dung file. `apply` tạo SA trên endpoint cấu hình; không in secret.
Script không tự xoay secret/xóa tài khoản hoặc ghi đè SA đã tồn tại. Khi một lần
chạy thất bại một phần, giữ file credentials và kiểm tra inventory trong MinIO
trước khi retry. Tạo SA không sửa nội dung object hoặc bucket policy.

## Runtime và nguồn MinIO

Catalog local hiện có file nguồn trong `unilake-raw`. Đã tạo SA thứ tư
`ingestion-source-reader` (access key `source-reader`), chỉ có GetObject,
GetObjectVersion và GetBucketLocation trên bucket nguồn này. Không mở rộng
quyền reader Bronze sang bucket nguồn và không dùng root để đọc nguồn.

Các lệnh dưới đây dùng khi cài môi trường mới, không chạy lại `prepare`/`apply`
nếu tài khoản/file đã tồn tại:

```bash
PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts prepare-source --credentials-file .env.source-reader.local
PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts apply-source --credentials-file .env.source-reader.local --source-bucket unilake-raw
PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts export-runtime
docker compose up -d --no-deps backend ingestion-worker
```

`export-runtime` tạo `.env.bronze-backend.local` chỉ chứa reader Bronze/nguồn và
`.env.bronze-worker.local` chứa writer/verifier/reader Bronze và reader nguồn.
Các file có mode 600, được Git ignore; không tự ghi đè nếu credentials khác.
Compose nạp từng file cho đúng service. Khi chạy Python trên host, Settings nạp
`.env.bronze-accounts.local` và `.env.source-reader.local` sau `.env`.

Trong `store_bronze`, writer chỉ kiểm tra versioning/upload; verifier mới gọi
stat/get để xác minh metadata và checksum. Resolver chọn reader Bronze nếu bucket
là Bronze; bucket nguồn khác dùng SOURCE_MINIO credentials riêng. Pipeline muốn
đọc Parquet trực tiếp phải nhận `bronze_reader_client()`; không dùng client admin.

## Quyền đọc downstream (UDL-142)

Định danh ETL/ELT đọc Bronze là SA `bronze-pipeline-reader`, access key
`bronze-reader`; credentials được cấp cho `bronze_reader_client()`. Repo hiện
không có compute-cluster IAM role hoặc deployment ETL worker riêng; SilverPipeline
là thư viện được caller điều phối. Verifier là bước xác minh, không phải identity
ETL; source-reader chỉ đọc nguồn ngoài Bronze.

Inline policy đã gắn vào SA downstream cấp GetObject/GetObjectVersion trên
`arn:aws:s3:::bronze/*`, GetBucketLocation/ListBucket/ListBucketVersions trên
`arn:aws:s3:::bronze`. Đồng thời Deny `s3:PutObject*`, `s3:DeleteObject*` và
AbortMultipartUpload trên Bronze objects, gồm cả ghi đè, sửa tags và xóa version.
Không cấp quyền ghi Silver từ identity đọc Bronze; writer Silver là quyền riêng.

Cập nhật và xác minh policy mà không đổi secret:

```bash
PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts update-reader
```

Ngày 18/10/2026: 9 tests pass (8 offline, 1 live) sau cập nhật. Live test xác nhận
Get, ListBucket và ListBucketVersions hoạt động; Put, Delete có/không version và
sửa object tags bị AccessDenied. Object thử nằm trong prefix UUID riêng đã được
dọn. Đây là bảo vệ bất biến trước identity downstream, không phải cấu hình WORM
Object Lock chống cả tài khoản quản trị.

Kiểm chứng offline nằm trong `backend/tests/test_minio_service_accounts.py`.
Kiểm thử quyền thật nằm trong `backend/tests/test_minio_service_accounts_live.py`:

```bash
BRONZE_IAM_LIVE_TEST=1 .venv/bin/python -m pytest backend/tests/test_minio_service_accounts.py backend/tests/test_minio_service_accounts_live.py -o addopts='' -q
```

Ngày 18/10/2026: 8 tests pass (7 offline, 1 live). Writer Put thành công; Get,
GetObjectVersion, DeleteObject và DeleteObjectVersion bị AccessDenied. Hai reader
đọc đúng bytes từ version vừa ghi nhưng bị chặn Put/Delete. Test chỉ dùng object
synthetic UUID riêng và dọn các version đó bằng admin.

Sau chuyển runtime: 74 tests offline pass, 4 tests live pass (API/worker đến Bronze
và downstream reader cho 3 domain, cùng test IAM), thêm 2 tests đọc nguồn catalog
CSV/Excel bằng source-reader pass. Live execution dùng schema PostgreSQL/prefix
UUID riêng, không nới policy SA. Backend được tái tạo và worker đã khởi động bằng
env SA; kiểm tra trong container xác nhận không có credentials admin, writer đọc
được cấu hình versioning và source-reader đọc được metadata file nguồn thật.
