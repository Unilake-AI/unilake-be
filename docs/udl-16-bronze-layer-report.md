# Báo cáo bàn giao UDL-16: Quản lý Bronze Layer

Ngày cập nhật: **18/10/2026** (Asia/Ho_Chi_Minh).

Đối tượng đọc: member phụ trách ingestion, pipeline Bronze → Silver, data governance,
DevOps và testing. Báo cáo mô tả code và các kiểm chứng đã thực hiện trên môi trường
local trong phiên triển khai; không phải xác nhận triển khai production hoặc phê
duyệt thay đổi trạng thái Jira.

- Story: [UDL-16 — Quản lý Bronze Layer](https://photopose.atlassian.net/browse/UDL-16).
- Phụ thuộc: UDL-12 cung cấp luồng nhận nguồn, tạo job và worker ingestion.
- Kết quả bàn giao: Bronze có writer thực tế, metadata truy vết, version/checksum,
  các Service Account (SA) giới hạn quyền và client runtime đã chuyển sang SA.
- Điểm cần chốt khi nghiệm thu: quyền được tách theo client; worker vẫn giữ cả
  writer và verifier trong cùng process. Nếu yêu cầu ingestion service hoàn toàn
  write-only ở cấp process, cần tách verifier thành service riêng.

## 1. Mục tiêu và ranh giới

UDL-16 yêu cầu giữ dữ liệu gần nguồn ban đầu, lưu trong Bronze, xác định được nguồn
gốc và cho phép pipeline phía sau đọc lại. Bronze không thực hiện schema mapping,
chuẩn hóa kiểu, resolve identity hoặc tính chỉ số nghiệp vụ.

| Thành phần | Trách nhiệm |
| --- | --- |
| PostgreSQL | Catalog nguồn/dataset, hàng đợi, trạng thái và output của ingestion job |
| MinIO Bronze | Lưu file nguồn hoặc snapshot bảng cùng version và metadata |
| Ingestion worker | Capture nguồn, upload Bronze, xác minh và hoàn tất job |
| Pipeline downstream | Đọc Bronze bằng SA chỉ đọc, chuẩn hóa và công bố Silver theo workflow riêng |

`unilake-raw` hiện là bucket chứa một số file **nguồn** trong catalog local.
Không đồng nhất bucket này với bucket đích `bronze`. File ở nguồn chưa tự chứng
minh một lần ingestion đã lưu Bronze thành công.

## 2. Đối chiếu các subtask

Đánh giá dưới đây là trạng thái kỹ thuật, không phải trạng thái Jira mới nhất.

| Subtask | Phần đã có | Giới hạn/điểm bàn giao |
| --- | --- | --- |
| [UDL-139: Storage Setup](https://photopose.atlassian.net/browse/UDL-139) | MinIO, bucket Bronze, versioning, Docker volume, quy ước partition; CLI local/upload | Volume cũ phải được giữ; khởi tạo bucket không thay backup |
| [UDL-140: Raw Data Persistence](https://photopose.atlassian.net/browse/UDL-140) | Worker nối capture → writer; CSV/Excel giữ nguyên bytes; PostgreSQL xuất snapshot Parquet | Chưa có reader REST API/JSON/XML; PostgreSQL là full snapshot, chưa CDC |
| [UDL-141: Metadata & Provenance](https://photopose.atlassian.net/browse/UDL-141) | Tự gắn source-system-id, ingestion-timestamp, batch-id, sha256; kiểm tra lại metadata và giữ trong output job | Áp dụng cho worker; upload thủ công bằng CLI không tự có catalog/job context |
| [UDL-142: Access Control & Downstream](https://photopose.atlassian.net/browse/UDL-142) | SA/policy đã tạo và gán trên MinIO local; client runtime theo vai trò; không fallback root; reader đọc/list nhưng không sửa/xóa Bronze | Writer và verifier còn chung process; chưa có ETL compute-cluster role/deployment riêng |
| [UDL-143: Testing & Validation](https://photopose.atlassian.net/browse/UDL-143) | Unit test, test IAM thật, API/worker → Bronze cho ba domain, kiểm tra metadata và dọn dummy data | Bằng chứng local/synthetic theo các nhóm bên dưới, không chứng minh toàn repo hoặc production đã nghiệm thu |

## 3. Flow thực thi

```text
POST /api/v1/ingestion/jobs
    |
    v
Validate nguồn, dataset, quyền và job đang hoạt động
    |
    v
Commit job QUEUED trong PostgreSQL
    |
    v
ingestion-worker claim job → RUNNING / READING
    |
    +--> Nguồn local / PostgreSQL: dùng reader tương ứng
    +--> File MinIO nguồn: source-reader chỉ đọc bucket nguồn được cấp quyền
    |
    v
Capture file riêng + SHA-256 → WRITING
    |
    v
bronze-writer upload object và metadata vào Bronze
    |
    v
bronze-verifier stat/get đúng version, đối chiếu metadata và checksum
    |
    v
Commit SUCCESS cùng URI/version/checksum/metadata trong output job
    |
    v
bronze-reader đọc đúng version làm đầu vào downstream
```

API trả HTTP 202 sau khi lưu hàng đợi. Worker chạy riêng, không dùng background
task trong tiến trình web để bảo đảm job tồn tại sau restart web.

Luồng worker hiện kết thúc tại Bronze. Không có bước tự động gọi SilverPipeline
ngay khi job SUCCESS. Member pipeline cần bổ sung điều phối theo job/event riêng.

## 4. Dữ liệu và quy ước lưu trữ

Object key do worker tạo:

```text
s3://<MINIO_BUCKET_BRONZE>/<dataset.domain>/YYYY/MM/DD/job-<job_id>/<filename>

Ví dụ minh họa:
s3://bronze/admissions/2026/10/18/job-42/applicants.csv
```

- Ngày partition lấy từ thời điểm tạo job, quy đổi sang Asia/Ho_Chi_Minh.
- CSV/Excel: copy file riêng, parse và upload cùng bản sao; không dựng lại file
  từ rows để tránh thay delimiter, newline, workbook hoặc các bytes khác.
- PostgreSQL: reader trả dữ liệu typed, capture xuất `snapshot.parquet`; không
  chuyển tất cả thành chuỗi hoặc áp dụng biến đổi nghiệp vụ trước Bronze.
- SHA-256 tính trên file sẽ được upload; version đích phải tồn tại và khác `null`.
- Dữ liệu nguồn không bị xóa sau khi ingest. Bucket Bronze bật versioning.
- Bộ nhớ đọc hiện materialize toàn bộ kết quả; chưa phải luồng streaming dữ liệu lớn.

## 5. Metadata và điều kiện công bố

| Metadata object | Nguồn giá trị | Ý nghĩa |
| --- | --- | --- |
| `source-system-id` | ID SourceSystem từ catalog đã validate | Định danh hệ thống nguồn ổn định |
| `ingestion-timestamp` | Worker tạo context trước bước lấy dữ liệu | ISO 8601 UTC; khác thời điểm enqueue |
| `batch-id` | `job-<ingestion_job_id>` | Liên kết object với lần ingestion |
| `sha256` | Nội dung file capture | Kiểm tra tính toàn vẹn |

MinIO trả các trường này qua header `x-amz-meta-*`. Không gắn credentials hoặc
dữ liệu từng record vào metadata. Muốn tìm DataSource/dataset cụ thể, tra job qua
batch ID thay vì coi source-system-id là ID DataSource.

`ingestion_job.output` lưu các trường:

```json
{
  "uri": "s3://bronze/admissions/2026/10/18/job-42/applicants.csv",
  "version_id": "<object-version-id>",
  "sha256": "<content-sha256>",
  "size_bytes": 123,
  "source_version_id": "<source-version-id-or-null>",
  "metadata": {
    "source-system-id": "7",
    "ingestion-timestamp": "2026-10-18T01:30:00+00:00",
    "batch-id": "job-42",
    "sha256": "<content-sha256>"
  }
}
```

Ví dụ trên chỉ minh họa cấu trúc, không phải receipt của một job thật.
Chỉ dùng output của job **SUCCESS** làm bằng chứng dữ liệu đã được công bố.
Không scan bucket rồi coi mọi object là dữ liệu ingestion thành công.

PostgreSQL và MinIO không cùng transaction. Nếu crash/lỗi sau upload nhưng trước
commit metadata, object có thể còn lại dù job không SUCCESS. Chưa có garbage
collector; không tự xóa object theo tuổi file hoặc theo suy đoán.

## 6. Service Account và quyền runtime

Các SA sau đã được tạo trên MinIO local; môi trường khác phải provisioning riêng.
Access key bên dưới là định danh, không phải secret key.

| Tên SA | Access key | Phạm vi và quyền |
| --- | --- | --- |
| bronze-ingestion-writer | `bronze-writer` | Put/multipart trong Bronze, đọc cấu hình location/versioning; Deny đọc/xóa object/version |
| bronze-verifier-reader | `bronze-verifier` | Get/GetObjectVersion trong Bronze để xác minh; không Put/Delete |
| bronze-pipeline-reader | `bronze-reader` | Get/GetObjectVersion, ListBucket/ListBucketVersions trong Bronze; Deny PutObject*, DeleteObject*, AbortMultipartUpload |
| ingestion-source-reader | `source-reader` | Chỉ Get/GetObjectVersion và location trên `unilake-raw`; không ghi/xóa |

SA là child credentials của MinIO, không phải AWS IAM Role hay role PostgreSQL.
Policy downstream bảo vệ cả bytes, tags và version trước identity downstream;
không phải WORM/Object Lock chống tài khoản quản trị. Writer vẫn có thể tạo
version mới khi upload cùng key; thiết kế không cấp quyền xóa version cũ.

| Runtime | Credentials được nạp |
| --- | --- |
| Backend | Reader Bronze và reader nguồn; không có writer/verifier/root |
| Ingestion worker | Writer, verifier, reader Bronze và reader nguồn; không có root |
| Provisioning/CLI quản trị | Credentials admin khi chạy tác vụ quản trị tường minh |

Backend đã được tái tạo và ingestion-worker đã khởi động bằng cấu hình SA trong
phiên kiểm chứng. Đã kiểm tra boolean bên trong container: không có credentials
admin; backend không có credentials writer; worker kiểm tra được versioning và
đọc được metadata file nguồn bằng SA tương ứng. Đây là quan sát lúc triển khai,
không phải trạng thái được giám sát liên tục.

Các hàm cần dùng trong code mới:

```python
from app.infra.minio.client import (
    bronze_reader_client,
    bronze_verifier_client,
    bronze_writer_client,
    source_reader_client,
)
```

Thiếu credentials SA sẽ báo `MINIO_<ROLE>_CREDENTIALS_REQUIRED`; không fallback
sang root. Hàm `app.infra.minio.bronze.client()` là client quản trị, không dùng
cho runtime pipeline mới. Reader Bronze không có quyền ghi Silver: publisher
Silver phải có identity/quyền riêng, không mở rộng quyền ghi trên Bronze.

## 7. Kết quả kiểm chứng

Các nhóm dưới đây được chạy ở các thời điểm trong quá trình triển khai và có
thể chồng lặp test; **không cộng các số thành tổng test duy nhất**.

| Nhóm | Kết quả đã ghi nhận | Phạm vi bằng chứng |
| --- | --- | --- |
| Metadata + execution + Bronze storage sau UDL-141 | 49 pass | Metadata, capture bytes/typed data, execution và lưu trữ offline |
| Regression sau chuyển client runtime | 74 pass | Client theo vai trò, thiếu credentials, phân phối env, metadata, resolver, file handoff và execution |
| API/worker/IAM live khi chuyển runtime | 4 pass | Ba case domain + một IAM case, PostgreSQL/MinIO thật, dữ liệu test riêng |
| Đọc DataSource nguồn MinIO bằng source-reader | 2 pass | CSV/Excel đã có trong catalog local |
| IAM sau tăng cường policy downstream | 9 pass | 8 offline + 1 live; Get/List hoạt động, ghi/xóa/sửa tags bị chặn |
| Nghiệm thu dummy data cuối cùng | 1 live pass | Ghi → đọc → negative tests → Admin dọn sạch mọi version/delete marker |

Ở lần nghiệm thu cuối, test kiểm tra cả nội dung và version hiện tại vẫn nguyên
vẹn sau các ca negative. Cleanup chỉ xử lý đúng object có UUID do test tạo, rồi
list lại để xác nhận không còn version/delete marker. Không tác động raw data
đã có. Ruff đạt; mypy đạt trên các module được kiểm tra trong phạm vi thay đổi.
Không tuyên bố full repository suite hoặc mọi migration production đã đạt.

## 8. File code để member đọc tiếp

Các đường dẫn tương đối tính từ repo root; phần thay đổi chính có comment tiếng Việt.

| File | Vai trò |
| --- | --- |
| [bronze_capture.py](../backend/app/bronze_capture.py) | Capture, Parquet export, upload metadata, xác minh bằng verifier |
| [contracts.py](../backend/app/modules/ingestion/contracts.py) | CapturedArtifact, BronzeWriteContext, BronzeBatch và provenance |
| [execution.py](../backend/app/modules/ingestion/execution.py) | Validate context, truyền nguồn/thời gian capture, stage và finish job |
| [ingestion_worker.py](../backend/app/ingestion_worker.py) | Claim job, heartbeat và vòng lặp thực thi |
| [ingestion_repo.py](../backend/app/repositories/ingestion_repo.py) | PostgreSQL queue, lease và cập nhật trạng thái |
| [client.py](../backend/app/infra/minio/client.py) | Factory client writer/verifier/reader/source theo credentials riêng |
| [service_accounts.py](../backend/app/infra/minio/service_accounts.py) | Sinh credentials, provisioning, update policy và export env runtime |
| [bronze.py](../backend/app/infra/minio/bronze.py) | Object key/partition, CLI và client quản trị |
| [minio_file_resolver.py](../backend/app/modules/ingestion/readers/minio_file_resolver.py) | Chọn SA chỉ đọc, pin version và tải file tạm |
| [source_loader.py](../backend/app/modules/ingestion/readers/source_loader.py) | API đọc thô và handoff batch có provenance cho file |
| [config.py](../backend/app/core/config.py) | Nạp cấu hình SA, không có root default dùng ngầm |
| [docker-compose.yml](../docker-compose.yml) | Nạp env khác nhau cho backend/worker; storage init riêng |
| [test_bronze_metadata.py](../backend/tests/test_bronze_metadata.py) | Metadata, timezone, ba domain và chặn thiếu metadata |
| [test_minio_runtime_clients.py](../backend/tests/test_minio_runtime_clients.py) | Tách client, fail-closed và không xuất root/writer vào backend |
| [test_minio_service_accounts.py](../backend/tests/test_minio_service_accounts.py) | Phạm vi policy, gán/cập nhật SA và credentials |
| [test_minio_service_accounts_live.py](../backend/tests/test_minio_service_accounts_live.py) | Nghiệm thu IAM thật và cleanup synthetic object |
| [test_ingestion_execution_live.py](../backend/tests/test_ingestion_execution_live.py) | API/queue/worker/Bronze cho ba domain, lỗi và lease recovery |

## 9. Chạy lại trên môi trường của member

Tất cả lệnh dưới đây chạy từ repo root trên macOS/Linux, dùng virtualenv đã cài
dependencies của backend. Mỗi member tự cấu hình endpoint, DB, bucket và secret;
không copy secret trong báo cáo hoặc commit vào Git.

### 9.1. Môi trường mới chưa có SA

Tạo file credentials/env trước vì Compose cần các file runtime khi parse cấu
hình. Bước prepare/export chỉ tạo file local, chưa gọi MinIO. Sau đó khởi động
storage, tạo bucket và cấp SA:

```bash
PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts prepare
PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts prepare-source --credentials-file .env.source-reader.local
PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts export-runtime

docker compose up -d minio postgres
docker compose run --rm minio-init

PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts apply
PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts apply-source --credentials-file .env.source-reader.local --source-bucket unilake-raw
docker compose up -d --build backend ingestion-worker
```

`unilake-raw` là nguồn của môi trường local đã kiểm tra, không phải bucket mặc
định bắt buộc cho mọi member. Chỉ tạo source SA khi có nguồn tương ứng; thay tên
bucket theo catalog môi trường. Với database có volume cũ, kiểm tra/áp dụng
migration ingestion trước khi chạy worker, xem [ingestion-execution.md](ingestion-execution.md).

### 9.2. Môi trường đã có SA và file credentials

Không chạy lại `prepare`/`apply` một cách mù quáng. `prepare` từ chối ghi đè file;
`apply` không tự reconcile SA đã tồn tại. Nếu tạo dở, kiểm tra inventory trước
khi retry; không xoay secret hoặc xóa SA đang được service sử dụng.

```bash
PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts update-writer
PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts update-reader
PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts export-runtime
docker compose up -d --no-deps backend ingestion-worker
```

`export-runtime` không ghi đè env có credentials khác; đổi/rotate secret cần
workflow triển khai riêng, không xóa file đang dùng chỉ để vượt qua kiểm tra này.

| File local, Git ignored | Mục đích |
| --- | --- |
| `.env.bronze-accounts.local` | Credentials ba SA Bronze, mode 600 |
| `.env.source-reader.local` | Credentials SA đọc nguồn, mode 600 |
| `.env.bronze-backend.local` | Env đọc cho backend, mode 600 |
| `.env.bronze-worker.local` | Env các client worker, mode 600 |

Các file không đi theo Git. Chỉ pull code chưa đủ để tái tạo tài khoản/secret
đã tạo trên MinIO máy khác. Admin credentials chỉ cần cho provisioning và các
fixture cleanup quản trị; không đưa vào env của backend/worker.

### 9.3. Lệnh kiểm thử

Offline, không kết nối DB/MinIO thật:

```bash
.venv/bin/python -m pytest backend/tests/test_bronze_metadata.py backend/tests/test_ingestion_execution.py backend/tests/test_bronze_storage.py backend/tests/test_minio_runtime_clients.py backend/tests/test_minio_service_accounts.py backend/tests/test_minio_file_resolver.py backend/tests/test_file_handoff.py -o addopts='' -q
```

Nghiệm thu quyền và cleanup dummy data trên MinIO thật:

```bash
BRONZE_IAM_LIVE_TEST=1 .venv/bin/python -m pytest backend/tests/test_minio_service_accounts_live.py -o addopts='' -v
```

API → worker → Bronze với ba domain trên PostgreSQL/MinIO thật:

```bash
INGESTION_LIVE_TEST=1 .venv/bin/python -m pytest backend/tests/test_ingestion_execution_live.py -o addopts='' -v
```

Live tests cần SA đã provisioning, bucket Bronze bật versioning, DB truy cập
được và quyền admin dành riêng cho fixture setup/cleanup. Execution test dùng
schema PostgreSQL/prefix UUID riêng nhưng vẫn tạo/xóa dữ liệu thử trên dịch vụ
thật; không bật tùy tiện trên môi trường dùng chung nếu chưa thống nhất.

## 10. Hướng dẫn member làm task tiếp theo

### Pipeline Bronze → Silver

1. Lấy URI, version và checksum từ job SUCCESS, không lấy object latest ngầm.
2. Dùng `bronze_reader_client()` đọc đúng version; đóng response/release connection.
3. CSV/Excel có thể đi qua snapshot/reader-to-batch để tạo binding, locator và
   provenance. `read_data_source_batch()` vẫn đọc location trong catalog; caller
   phải bảo đảm location/version là object Bronze đã công bố, không trỏ nhầm file nguồn.
4. Snapshot PostgreSQL lưu Parquet chưa có adapter tương đương đi qua
   `read_data_source_batch()`; cần adapter Parquet có provenance riêng.
5. Chọn rõ profile/version S2 → S3 → S4 trước SilverPipeline; quyền reader không
   thay thế các kiểm tra schema, DQ, identity và governance của Silver publisher.
6. Cấp quyền ghi Silver riêng cho publisher. Không cấp Put/Delete Bronze để tiện debug.

### DevOps và vận hành

- Chốt yêu cầu write-only ở cấp client hay process; tách verifier nếu cần cấp process.
- Thêm quy trình secret rotation/deployment cho môi trường dùng chung/production.
- Thống nhất backup, retention, quota và orphan cleanup có đối chiếu metadata.
- Bucket/prefix permission hiện là phạm vi service, chưa có chính sách riêng từng domain.
- Không dùng `docker compose down -v` nếu cần giữ dữ liệu local.

### Testing và nghiệm thu

- Giữ test positive, negative và xác nhận cleanup; lỗi mạng không thay cho AccessDenied.
- Bổ sung loại dữ liệu/nguồn khi mở rộng reader; không suy test CSV đại diện JSON/XML.
- Bổ sung tải lớn, race nhiều worker, restart và lỗi hạ tầng theo phạm vi task tiếp theo.
- Đính kèm kết quả CI/môi trường đích khi review; không coi số test local là production approval.

## 11. Tài liệu liên quan

- [Bronze storage](bronze-storage.md): cấu trúc partition, storage và upload CLI.
- [Ingestion execution](ingestion-execution.md): API/job/worker, trạng thái và migration.
- [Service Account Bronze](bronze-service-accounts.md): policy, provisioning và runtime.
- [Silver integration P5](standardization/silver-integration-p5.md): snapshot publication,
  governance gate và giới hạn tích hợp Silver.

Các thay đổi code/docs của đợt bàn giao còn cần đi qua commit/PR/review theo quy
trình nhóm. Báo cáo này không tự stage, commit, push hoặc đổi trạng thái Jira.
