# Ingestion Execution Service

## Luồng thực thi

```text
POST /api/v1/ingestion/jobs
  -> kiểm tra quyền, source, dataset, loại reader và job đang chạy
  -> commit QUEUED vào app.ingestion_job
  -> worker claim bằng SELECT ... FOR UPDATE SKIP LOCKED
  -> commit RUNNING / READING
  -> capture file và đọc bằng reader
  -> commit WRITING + rows_read
  -> ghi MinIO Bronze, đọc lại đúng version để kiểm tra SHA-256
  -> commit SUCCESS + output + thống kê nguồn
```

Job được lưu trước khi đọc nguồn; API trả `202` ngay sau khi commit hàng đợi.
PostgreSQL là hàng đợi bền vững, không phụ thuộc background task trong tiến trình
web. Nhiều worker có thể chạy song song; mỗi worker xử lý một job tại một thời điểm.
Một nguồn chỉ có tối đa một job `QUEUED`/`RUNNING`. Khóa nguồn lúc enqueue và
unique partial index bảo vệ cả trường hợp có nhiều API process.

Phạm vi là Source → Bronze. Không tự chạy mapping, Silver hoặc Gold.

## API

Gửi bearer token có role `ADMIN` hoặc `DATA_ADMIN`:

```http
POST /api/v1/ingestion/jobs
Authorization: Bearer <token>
Content-Type: application/json

{
  "data_source_id": 1,
  "dataset_id": 1,
  "source_table": "applicants.csv"
}
```

ID minh họa cần thay bằng catalog thực tế. Dataset phải `ACTIVE`, thuộc layer
`BRONZE`, có cùng `source_system_id` với nguồn. `dataset.domain` là slug partition
(ví dụ `admissions`, `hr`). Không nhận location/credential tùy ý từ request;
worker lấy chúng từ catalog và kiểm tra lại khi bắt đầu chạy.

Theo dõi:

- `GET /api/v1/ingestion/jobs/{ingestion_job_id}`: chi tiết job.
- `GET /api/v1/ingestion/jobs?data_source_id=1&limit=20&offset=0`: lịch sử mới nhất trước.
- Quyền đọc: `ADMIN`, `DATA_ADMIN`, `ANALYST`, `VIEWER`, theo quy ước catalog hiện tại.

Response có `status`, `stage`, `requested_by`, các mốc thời gian UTC,
`rows_read`, `rows_written`, `rows_rejected`, `error_message` và `output`.
`output` khi thành công gồm URI S3, version ID, SHA-256, kích thước và version
nguồn nếu đọc từ MinIO. Thời gian dùng timestamp UTC không timezone theo schema
hiện có; client phải diễn giải là UTC.

## Trạng thái và lỗi

| Tình huống | Kết quả |
|---|---|
| Hợp lệ | HTTP 202, `QUEUED` → `RUNNING` → `SUCCESS` hoặc `FAILED` |
| Nguồn đã có job hoạt động | HTTP 409, tạo lịch sử `REJECTED`, mã `SOURCE_ALREADY_RUNNING` |
| Source/System tắt, dataset sai layer/nguồn, reader chưa hỗ trợ | HTTP 422, lưu `REJECTED`, trả job ID trong `error.details` |
| Không có quyền | HTTP 401/403, không tạo job |
| Source/dataset không tồn tại | HTTP 404, không tạo job vì không có đối tượng catalog hợp lệ để liên kết |
| Payload sai | HTTP 422 từ Pydantic, không tạo job |
| PostgreSQL không ghi nhận được job | HTTP 503 `INGESTION_QUEUE_UNAVAILABLE`; không thể bảo đảm audit khi DB không truy cập được |
| Lỗi đọc nguồn | `FAILED`, `INGESTION_READING_FAILED` |
| Lỗi ghi/versioning/checksum Bronze | `FAILED`, `INGESTION_WRITING_FAILED` |
| Lỗi hoàn tất metadata | `INGESTION_COMMITTING_FAILED` nếu DB còn ghi được; nếu DB mất kết nối, xử lý qua lease |
| Worker chết hoặc mất quyền thực thi | Worker đang hoạt động quét và chuyển lease hết hạn sang `FAILED`, `WORKER_LEASE_EXPIRED` |

Worker gia hạn lease mỗi 15 giây, thời hạn 120 giây. Cập nhật stage/kết quả chỉ
được phép khi job còn `RUNNING` và lease chưa hết hạn. Job hết hạn không được
tự động retry để tránh lặp việc đọc nguồn có thể đã thay đổi; gửi POST mới để
chạy lại với job ID khác. Khi không có worker, job nằm `QUEUED`; khởi động worker
sẽ tiếp tục lấy hàng đợi. Khi tất cả worker dừng, việc đánh dấu lease hết hạn diễn
ra ở lần worker chạy tiếp theo.

`ingestion_count` đếm lần **thành công**; `last_ingested_at` chỉ đổi khi thành công.
Lần bị từ chối không ghi đè trạng thái của job đang chạy. Không loại bỏ từng dòng:
job thành công có `rows_written = rows_read`, `rows_rejected = 0`.
Thông báo lỗi public không chứa exception gốc có thể lộ password hoặc dữ liệu.

## Dữ liệu Bronze

### Metadata và truy xuất nguồn gốc (UDL-141)

Worker tự gắn user metadata vào từng object Bronze khi upload; MinIO biểu diễn
chúng bằng header `x-amz-meta-*`. Không cần migration database: cùng metadata
được lưu trong `ingestion_job.output.metadata` (JSONB) để đối chiếu.

| Metadata key | Giá trị |
| --- | --- |
| `source-system-id` | ID hệ thống nguồn từ catalog đã validate |
| `ingestion-timestamp` | Thời điểm worker bắt đầu lấy dữ liệu, ISO 8601 UTC |
| `batch-id` | `job-<ingestion_job_id>`, trùng batch trong object key |
| `sha256` | SHA-256 của đúng file được upload |

Thời điểm capture không phải thời điểm enqueue (`created_at`); partition ngày
vẫn dùng ngày tạo job theo quy ước hiện có. Metadata không chứa credentials
hay dữ liệu từng bản ghi. Writer kiểm tra metadata và checksum trên đúng
`version_id` vừa ghi. Thiếu hoặc sai metadata gây `BRONZE_METADATA_MISMATCH`
nội bộ, job công khai mã `INGESTION_WRITING_FAILED` và không công bố thành công.
Object đã upload khi validation thất bại có thể còn trên storage như các lỗi ghi
khác; không tự xóa phiên bản đó.

Test offline: `backend/tests/test_bronze_metadata.py`. Test opt-in
`backend/tests/test_ingestion_execution_live.py` cũng đối chiếu metadata thật
với kết quả job khi chạy với PostgreSQL/MinIO. Đường CLI upload thủ công ở
`app.infra.minio.bronze` không có catalog/job context, không thuộc cơ chế gắn
metadata tự động của worker này.

- CSV/Excel: copy riêng trước khi parse; lưu đúng file gốc, không ghi lại từ các
  dòng đã parse. Source là S3 được resolve và giữ version nguồn.
- PostgreSQL: reader hiện có đọc snapshot bảng vào bộ nhớ; export thành Parquet
  để giữ kiểu dữ liệu thay vì chuyển tất cả sang chuỗi. Đây là full snapshot,
  không phải CDC hay incremental ingestion. Reader cần DuckDB extension `postgres`
  được cài sẵn (`LOAD postgres`). Dockerfile cài extension cho runtime user;
  khi chạy local cần cài cho phiên bản DuckDB đang dùng. Thiếu extension sẽ tạo job `FAILED`.
- REST API/JSON chưa có reader nên bị từ chối khi khởi chạy.
- Key: `<dataset.domain>/YYYY/MM/DD/job-<id>/<filename>`. Ngày tính từ lúc tạo
  job theo `Asia/Ho_Chi_Minh`; bucket là `MINIO_BUCKET_BRONZE`.
- Chỉ job `SUCCESS` cùng URI/version trong `output` mới xác nhận publication.
  PostgreSQL và MinIO không cùng một transaction: crash sau upload có thể để lại
  object chưa được công bố. Không scan bucket rồi coi mọi object là dữ liệu thành công;
  không tự xóa object mồ côi trong worker.

Reader hiện tải toàn bộ kết quả vào bộ nhớ, phù hợp các batch có giới hạn dung lượng.
Hủy coroutine không ngắt được ngay I/O đang chạy trong thread; service chờ I/O kết
thúc trước khi dọn file tạm, nhưng không công bố thành công sau khi mất lease.

## Triển khai

Với volume đã có, từ thư mục gốc áp dụng migration **trước khi** chạy API/worker:

```powershell
Get-Content -Raw infra/postgres/migrations/20261015_ingestion_execution.sql | docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose up -d --build ingestion-worker
```

Migration chỉ thêm cột/index, chạy lại được. Database mới có schema ngay từ
`02-schema.sql`. Không xóa volume để chạy migration. Backend đang chạy cần reload
code hoặc restart để nhận route mới.

Worker dùng chung DB/MinIO settings với backend, chờ PostgreSQL healthy và
`minio-init` thành công. `./data` được mount read-only vào `/data`: nguồn local
phải có location container, ví dụ `/data/samples/...`, không phải đường dẫn `D:\...`.
Kết nối PostgreSQL nguồn trong Docker phải dùng hostname mà container truy cập
được; `localhost` trong container là chính container đó.

Chạy worker local từ `backend`, dùng `.env` với endpoint host:

```powershell
..\.venv\Scripts\python.exe -m app.ingestion_worker
```

## Kiểm thử

```powershell
# Từ backend
..\.venv\Scripts\python.exe -m pytest tests/test_ingestion_execution.py -q --no-cov
$env:INGESTION_LIVE_TEST = '1'
..\.venv\Scripts\python.exe -m pytest tests/test_ingestion_execution_live.py -q --no-cov
```

Test live tạo schema PostgreSQL riêng và prefix UUID trong bucket Bronze thật,
chạy worker bằng các SA runtime (writer/verifier/reader), chạy HTTP API,
enqueue đồng thời, nhiều worker, kiểm tra bytes/version, lỗi đọc/ghi, nguồn tắt,
lease hết hạn và bộ đếm. Chỉ dọn schema/bucket do test đó tạo.
