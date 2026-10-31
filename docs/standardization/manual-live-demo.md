# Hướng dẫn test chi tiết Live Demo: Source → Bronze → Silver

Cập nhật 16/10/2026. [Review luồng](bronze-silver-flow-review.md).
[Hướng dẫn upload trực tiếp cũ](manual-storage-demo.md) chỉ dành cho storage/normalization.

## Cách sử dụng tài liệu

Làm tuần tự từ bước 1 đến bước 10. Chỉ chuyển bước khi đã đạt kết quả mong đợi.
Lần đầu chạy một file Admissions đến Silver thành công, rồi mới thử domain khác và file lỗi.
Tất cả lệnh PowerShell chạy tại `D:\Caps1\UniDataLake_AI`. SQL chạy trong DBeaver/pgAdmin
hoặc psql; JSON request nhập ở Swagger, không dán vào PowerShell.

Cần mở Docker Desktop, PowerShell và trình duyệt. Chuẩn bị tài khoản ứng dụng có role
ADMIN hoặc DATA_ADMIN; tài khoản đăng nhập MinIO và PostgreSQL lấy từ `.env` của bạn.
Tài khoản ứng dụng không nhất thiết giống tài khoản MinIO hay database.

| Công cụ | Địa chỉ / mục đích |
|---|---|
| Swagger | http://localhost:8000/docs — đăng nhập, tạo và theo dõi job |
| MinIO Console | http://localhost:9001 — xem file Bronze và object Silver |
| PostgreSQL trên Windows | localhost:5434 mặc định — catalog, crosswalk, job và snapshot metadata |
| MinIO API | localhost:9000 — endpoint cho Python, không phải trang Console |

Phạm vi là dữ liệu giả lập và chế độ rehearsal. File Excel vẫn ở Bronze; dữ liệu Silver
được lưu thành Parquet trong MinIO. PostgreSQL giữ metadata và crosswalk.

## Luồng thực tế

```text
File trong data/standardization/live-demo
 → Docker mount /data/standardization/live-demo
 → catalog source + dataset BRONZE
 → POST /api/v1/ingestion/jobs → QUEUED
 → worker claim → RUNNING/READING/WRITING
 → MinIO bronze/<domain>/YYYY/MM/DD/job-<id>/<filename>
 → job SUCCESS, output.uri/version_id/sha256
 → chạy CLI --ingestion-job ID
 → đọc đúng version Bronze, kiểm tra checksum/rows
 → S2 mapping → S3 kiểu → S4 ID/dictionary
 → kiểm tra Silver, ghi/đọc lại Parquet và manifest
 → publish snapshot hoặc FAILED, giữ head cũ
```

Không upload tay vào Bronze trong buổi này: worker thực hiện bước đó. Silver vẫn cần
gọi CLI thủ công; chưa có tự động dispatch hoặc API Silver. CLI chỉ dùng catalog demo
DEMO_FLOW_* và rehearsal, không phải orchestration production.

## 1. File và môi trường

Dùng sáu XLSX trong `data/standardization/live-demo`, mỗi file một dòng, sheet Data.
Giữ nguyên header và text cho mã/ngày/Decimal trong lần đầu.

Kiểm tra file và Python trước:

```powershell
Set-Location D:\Caps1\UniDataLake_AI
Get-ChildItem .\data\standardization\live-demo\*.xlsx
.\.venv\Scripts\python.exe --version
```

Nếu chưa có `.venv`, chạy `py -3.11 -m venv .venv` trước khi cài dependencies bên dưới.

| File | Domain | Kỳ vọng Silver |
|---|---|---|
| 01-admissions.xlsx | admissions | 007 → canonical ID; 30,00 → Decimal; Có → true; bỏ description |
| 02-academic.xlsx | academic | Ngày ISO và status Ongoing |
| 03-hr.xlsx | hr | position_level Staff |
| 04-bad-date.xlsx | academic | DATE_PARSE_ERROR |
| 05-unknown-id.xlsx | hr | SOURCE_ID_UNMAPPED |
| 06-hr-updated.xlsx | hr | Tên mới, ID giữ nguyên |

```powershell
Set-Location D:\Caps1\UniDataLake_AI
docker compose up -d postgres minio
docker compose run --rm minio-init
docker compose ps -a
```

PostgreSQL/MinIO healthy, init exit 0. Không down -v. Python host dùng localhost:5434
(hoặc cổng thực tế công bố), MinIO localhost:9000, credentials trong `.env`.
Container dùng postgres:5432 và minio:9000 theo Compose.
Nếu thiếu Python dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -e "./backend[dev,ingest-docs]"
```

Nếu runner báo `ModuleNotFoundError: No module named 'psycopg'`, cài driver vào đúng
Python trên Windows rồi chạy lại; không cần build lại Docker:

```powershell
.\.venv\Scripts\python.exe -m pip install "psycopg[binary]>=3.2"
```

## 2. Migration và API/worker

Volume cũ cần migration ingestion sau; migration này chạy lại được. DB mới có trong init:

```powershell
docker compose cp infra/postgres/migrations/20261015_ingestion_execution.sql postgres:/tmp/ingestion.sql
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/ingestion.sql'
```

Trong DBeaver/pgAdmin kiểm tra:

Nếu không dùng công cụ GUI, mở psql bằng PowerShell:

```powershell
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Dán câu SQL dưới đây vào dấu nhắc psql. Gõ `\q` để thoát về PowerShell trước khi chạy
các lệnh Docker tiếp theo.

```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema='app' AND table_name IN
('canonical_identity','identity_crosswalk','crosswalk_release',
 'silver_run','silver_snapshot','silver_head') ORDER BY table_name;
```

Chỉ chạy migration S4/P5 tương ứng nếu cả nhóm ba bảng của nó chưa có; nếu đã đủ thì bỏ qua.
Nếu có một phần, đối chiếu schema trước. Hai migration này không có IF NOT EXISTS.

```powershell
docker compose cp infra/postgres/migrations/20261015_s4_crosswalk.sql postgres:/tmp/s4.sql
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/s4.sql'
docker compose cp infra/postgres/migrations/20261015_p5_silver.sql postgres:/tmp/p5.sql
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/p5.sql'
```

Sau migration, chạy/restart dịch vụ để dùng code mới:

```powershell
docker compose up -d --build backend ingestion-worker
docker compose logs --tail 40 ingestion-worker
docker compose exec ingestion-worker ls -l /data/standardization/live-demo
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py check
```

Lệnh `ls` phải liệt kê đủ sáu XLSX. Nếu build chậm và màn hình chỉ lặp tiến trình,
có thể dừng lệnh build bằng Ctrl+C rồi chạy log rõ hơn:

```powershell
docker compose --progress plain build backend ingestion-worker
docker compose up -d backend ingestion-worker
```

`CACHED` nghĩa là dùng lại bước build trước. Dòng tải pip/dependency chưa phải lỗi.
Chỉ chạy lệnh `up` sau khi build kết thúc thành công; không chạy nhiều build cùng lúc.

Compose kéo Redis theo dependency backend, dù ingestion queue dùng PostgreSQL.
Nếu backend local đang dùng cổng 8000, chọn một backend để tránh trùng cổng.
`check` cần missing_tables=[], buckets=true, bronze_versioning=Enabled. Check này kiểm tra
S4/P5 và storage, không thay thế kiểm tra migration ingestion/API.

## 3. Catalog và crosswalk demo

```powershell
docker compose cp scripts/standardization/seed_ingestion_demo.sql postgres:/tmp/seed-demo.sql
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/seed-demo.sql'
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py init-job-crosswalk
```

Seed tạo ba SourceSystem DEMO_FLOW_*, ba datasource EXCEL và ba dataset BRONZE, không
sửa nguồn nghiệp vụ hiện có. Không cố định ID 1/2/3. Ghi lại bảng domain/data_source_id/dataset_id
được in ra. Chạy seed tuần tự, không chạy đồng thời nhiều bản.
Seed chạy lại không tạo bản trùng theo mã/tên fixture và không tự sửa cấu hình bạn đã đổi.

Location phải là `/data/standardization/live-demo`; config sheet_name=Data, header_row=1,
skip_rows=0. Worker thấy thư mục nhờ mount `./data:/data:ro`. Không dùng đường dẫn D:\...
cho worker Linux. Không sửa catalog trong khi job đang chạy.

`init-job-crosswalk` cấp ID thống nhất cho 007/008/009 theo source_system_id thật và freeze
release `manual-ingestion-demo-v1`. ID phụ thuộc sequence DB, không có giá trị cố định.
Không dùng init-crosswalk cũ thay thế: nó dành cho demo upload trực tiếp. Không xóa/tạo lại
SourceSystem sau khi freeze; catalog ID mới cần crosswalk release mới.

### Lấy lại cặp ID nếu không còn kết quả seed

Chạy SQL này trong database dự án:

```sql
SELECT d.domain, ds.data_source_id, d.dataset_id, ds.location
FROM app.source_system s
JOIN app.data_source ds ON ds.source_system_id = s.source_system_id
JOIN app.dataset d ON d.source_system_id = s.source_system_id
WHERE s.source_code IN ('DEMO_FLOW_admissions','DEMO_FLOW_academic','DEMO_FLOW_hr')
  AND ds.source_name = s.source_code || '_FILES'
  AND d.dataset_code = s.source_code || '_BRONZE'
ORDER BY d.domain;
```

Phải có ba dòng academic/admissions/hr. Nếu không có, kiểm tra đã seed đúng database.
Điền kết quả thực tế vào bảng để dùng suốt buổi:

| Domain | data_source_id | dataset_id |
|---|---|---|
| admissions | … | … |
| academic | … | … |
| hr | … | … |

**Phân biệt ID:** data_source_id và dataset_id dùng để POST tạo job.
ingestion_job_id do API trả về sau khi tạo job, dùng để GET theo dõi và chạy Silver.
snapshot_id do Silver trả về sau publish, không dùng thay cho ID job.

## 4. Đăng nhập và tạo job Admissions

Mở http://localhost:8000/docs. Gọi POST /api/v1/auth/login bằng tài khoản ADMIN hoặc
DATA_ADMIN, lấy access_token, bấm Authorize và nhập token. Không lưu token vào tài liệu.

Thao tác cụ thể:

1. Mở endpoint login → **Try it out**.
2. Nhập `username` và `password` của tài khoản ứng dụng → **Execute**.
3. Trong response thành công, copy giá trị `access_token`, không lấy refresh_token.
4. Bấm **Authorize** ở đầu Swagger, dán token không có dấu ngoặc kép và không tự thêm Bearer.
5. Bấm **Authorize**, rồi **Close**.
6. Mở POST ingestion → **Try it out** và nhập body bên dưới với ID thật.

Đăng nhập thành công không tự gắn token vào mọi request Swagger: cần thao tác Authorize.
Nếu bị 401, kiểm tra phần Curl có header `Authorization: Bearer ...` chưa.
Không chụp/chia sẻ token hoặc mật khẩu khi gửi ảnh lỗi.

Gọi POST /api/v1/ingestion/jobs:

```json
{"data_source_id":123,"dataset_id":456,"source_table":"01-admissions.xlsx"}
```

123/456 là minh họa: thay bằng ID admissions của bước 3. Nhận HTTP 202 và ingestion_job_id.
Gọi GET /api/v1/ingestion/jobs/{job_id} tới SUCCESS/FAILED. Không POST lặp để chờ:
một source có job đang QUEUED/RUNNING sẽ trả 409 và lưu REJECTED.

Trong Swagger, chỉ **Server response** là kết quả thực tế của lần gọi.
Các mục **Responses → 202 Successful Response / 422 Validation Error → Example Value**
là tài liệu mẫu, không chứng minh job đã tạo. Job ID mẫu bằng 0 không phải ID thật.

Mở GET theo ID → Try it out → nhập ingestion_job_id → Execute. Nếu còn QUEUED/RUNNING,
đợi vài giây rồi Execute GET lại. Chỉ chạy Silver sau khi GET trả SUCCESS.

## 5. Kiểm tra Bronze trước Silver

Job đúng: status SUCCESS, stage COMPLETED, rows_read=rows_written=1, rows_rejected=0.
output phải có uri/version_id/sha256/size_bytes. Mở http://localhost:9001, vào bucket Bronze,
tìm key chính xác từ output, ví dụ admissions/YYYY/MM/DD/job-ID/01-admissions.xlsx.
Ngày partition là ngày tiếp nhận theo Asia/Ho_Chi_Minh. File giữ nguyên nội dung nguồn.
Silver chưa đổi. Object tồn tại không đủ xác nhận ingestion: phải có job SUCCESS.

## 6. Chạy Silver và retry

```powershell
$demoSession = "flow1"
$admJob = [int](Read-Host "Nhap ingestion_job_id Admissions da SUCCESS")
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py run --session $demoSession --ingestion-job $admJob --base none
```

Nếu flow1 đã được dùng trong buổi trước, chọn flow2 hoặc tên mới. Giữ nguyên session cho
cả buổi: file đúng, file lỗi và cập nhật phải dùng cùng session. Nếu mở PowerShell mới,
khai báo lại `$demoSession` bằng tên đã dùng. Các câu SQL lọc flow1 bên dưới phải đổi tương ứng.

Runner lấy domain/key/version từ job, đối chiếu catalog demo, key thuộc job, size/SHA-256/rows.
Không chọn version mới nhất thay cho version đã publish. Provenance có các ID catalog thật.
Mapping/parser/dictionary vẫn là profile fixture cố định, không tự suy luận mọi file.
Kỳ vọng PUBLISHED, input_rows=accepted_rows=1, receipt.snapshot_id có giá trị,
max_score="30.00" trong JSON (Parquet là Decimal), is_active=true, không có description.

Chạy lại NGUYÊN lệnh với cùng job và --base none: reused=true, snapshot ID không đổi.
Không POST ingestion mới để kiểm tra retry Silver: job mới sinh Bronze key mới.
Lưu report in ở cuối lệnh; report có ingestion_job_id và source_version.

## 7. Academic và HR

POST/GET như bước 4: dùng ID catalog Academic với 02-academic.xlsx, HR với 03-hr.xlsx.
Đợi từng job SUCCESS rồi dùng ID thật:

```powershell
$academicJob = [int](Read-Host "Nhap ingestion_job_id Academic da SUCCESS")
$hrJob = [int](Read-Host "Nhap ingestion_job_id HR da SUCCESS")
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py run --session $demoSession --ingestion-job $academicJob --base active
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py run --session $demoSession --ingestion-job $hrJob --base active
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py inspect --session $demoSession
```

Nhập ID job thật, không nhập data_source_id/dataset_id. Snapshot có ba entity, mỗi entity một dòng.
semester.start_date=2026-09-01, status=Ongoing, position.position_level=Staff.
inspect đọc metadata và Parquet thật từ MinIO.

## 8. Bronze SUCCESS nhưng Silver phải FAILED

### 8.1. Ghi lại trạng thái trước khi thử lỗi

```powershell
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py inspect --session $demoSession
```

Ghi lại active_snapshot, position_id và position_name. Tên HR lúc này dự kiến là
`Giảng viên giả lập`; ba entity admission_method, semester, position mỗi entity một dòng.

### 8.2. File sai ngày

1. Trong Swagger POST ingestion, dùng cặp ID **Academic**, source_table=`04-bad-date.xlsx`.
2. Execute, lấy ingestion_job_id từ Server response HTTP 202.
3. GET theo job ID đến SUCCESS, rows_read=rows_written=1.
4. Mở MinIO theo output.uri nếu muốn đối chiếu file Bronze.
5. Chạy lệnh dưới đây, nhập đúng job vừa tạo:

```powershell
$badDateJob = [int](Read-Host "Nhap ingestion_job_id cua 04-bad-date.xlsx")
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py run --session $demoSession --ingestion-job $badDateJob --base active
```

File có `tu=31/02/2026`. Reader đọc được chuỗi nên Bronze SUCCESS là đúng.
S3 không thể chuyển chuỗi này thành ngày hợp lệ. Kỳ vọng Silver status=FAILED,
accepted_rows=0, issues có DATE_PARSE_ERROR và field/locator liên quan.
head_before phải bằng head_after. Chạy inspect lại: dữ liệu và active snapshot vẫn như trước.
Không sửa file để làm ca lỗi này publish; bị từ chối đúng nguyên nhân chính là kết quả đạt.

### 8.3. Mã chưa có đối chiếu

1. POST ingestion với cặp ID **HR**, source_table=`05-unknown-id.xlsx`.
2. Lấy job ID mới, GET đến SUCCESS Bronze.
3. Chạy:

```powershell
$unknownIdJob = [int](Read-Host "Nhap ingestion_job_id cua 05-unknown-id.xlsx")
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py run --session $demoSession --ingestion-job $unknownIdJob --base active
```

File có `id="999"`, trong khi crosswalk HR demo đã chuẩn bị mã `"009"`.
Kỳ vọng FAILED, accepted_rows=0, issues có SOURCE_ID_UNMAPPED, head_before=head_after.
inspect vẫn thấy dòng HR cũ, không xuất hiện dòng mới do hệ thống tự đoán ID.
Không thêm 999 vào crosswalk trong lúc chạy ca lỗi này.

### 8.4. Cách kết luận hai ca lỗi

POST Academic với 04-bad-date.xlsx; POST HR với 05-unknown-id.xlsx. Đợi SUCCESS Bronze.
Hai file vẫn parse được: Bronze không kiểm tra ngày nghiệp vụ hoặc crosswalk.

Kỳ vọng FAILED, exit 2, DATE_PARSE_ERROR / SOURCE_ID_UNMAPPED và locator dòng.
head_before=head_after; inspect vẫn thấy ba entity hợp lệ cũ. Job Bronze vẫn SUCCESS vì
hai tầng có trạng thái riêng. Không có quarantine tự động hoặc tự lấy riêng dòng đúng.

## 9. Cập nhật và lỗi Bronze riêng

POST HR với 06-hr-updated.xlsx, đợi SUCCESS, dùng ID thật:

```powershell
$updatedJob = [int](Read-Host "Nhap ingestion_job_id cua 06-hr-updated.xlsx")
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py run --session $demoSession --ingestion-job $updatedJob --base active
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py inspect --session $demoSession
```

Thao tác Swagger giống ca HR trước: dùng cặp ID HR, đổi source_table thành
`06-hr-updated.xlsx`, POST lấy ID mới và GET đến SUCCESS trước khi chạy lệnh.
File vẫn dùng mã nguồn 009, chỉ thay tên. Kỳ vọng PUBLISHED, input_rows=accepted_rows=1.

| Đối chiếu | Trước | Sau |
|---|---|---|
| position_id | ID đã ghi lại | Giữ nguyên |
| position_name | Giảng viên giả lập | Giảng viên giả lập cập nhật |
| Số dòng position | 1 | 1 |
| active_snapshot | Snapshot cũ | Snapshot mới |
| Admissions và Academic | Dữ liệu đã có | Giữ nguyên |

Tên position đổi thành Giảng viên giả lập cập nhật, ID cũ, một dòng; entity khác giữ nguyên.
P5 thay toàn bộ entity được gửi, không append/upsert theo dòng. Muốn retry lần cập nhật,
dùng UUID base_snapshot trong report trước thay active (vì active đã đổi sau publish).

Thử lỗi Bronze: POST HR với source_table="missing.xlsx". API có thể QUEUED, worker sau đó
FAILED/INGESTION_READING_FAILED, không có output được publish. Đưa ID này vào runner Silver
phải bị chặn SUCCESSFUL_BRONZE_JOB_REQUIRED, chưa tạo Silver run.

## Chạy Cleaning qua Swagger

Endpoint thủ công:

```http
POST /api/v1/pipeline/cleaning/ingestion-jobs/{ingestion_job_id}
Authorization: Bearer <token có role ADMIN hoặc DATA_ADMIN>
Content-Type: application/json

{
  "session": "cleaning-demo",
  "base_snapshot": "active"
}
```

`base_snapshot` có thể là `"active"` để tiếp tục từ head của session, UUID snapshot
cụ thể để chỉ rõ parent, hoặc `null` để khởi tạo session mới. Chỉ dùng `session`
riêng cho rehearsal; mặc định endpoint ghi vào scope rehearsal, không phải production.
Trước đó cần:

1. Khởi động DB/MinIO, apply migrations và tạo catalog/crosswalk fixture của
   live demo; runner cần release crosswalk `manual-ingestion-demo-v1`.
2. Đảm bảo MinIO bucket Bronze và Silver có versioning/các cấu hình cần thiết.
   Từ repo root, nếu chưa provision service accounts:

   ```bash
   PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts prepare
   PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts apply
   PYTHONPATH=backend .venv/bin/python -m app.infra.minio.service_accounts export-runtime
   ```

   `prepare`/`apply` yêu cầu cấu hình MinIO admin cục bộ. Không commit hoặc chia sẻ
   file `.env*.local`; `export-runtime` tạo `.env.silver-backend.local` với
   credentials writer riêng, bị giới hạn trong bucket Silver. Restart backend để
   nạp credentials mới.
3. Tạo ingestion job bằng catalog `DEMO_FLOW_<domain>`, đợi job `SUCCESS`, rồi dùng
   đúng `ingestion_job_id` của job đó. Endpoint hiện chỉ nhận ba datasource demo,
   CSV/XLSX theo đúng profile phẳng của domain; không dùng file thật hoặc catalog
   nguồn khác.

Trong Swagger tại `/docs`, đăng nhập và gửi request. `PUBLISHED` trả report
cleaning cùng snapshot ID; `QUARANTINED` trả issue/counts và không publish Silver;
`EMPTY` nghĩa là không còn record để publish. Lỗi prerequisite trả mã lỗi trong
response, ví dụ `SUCCESSFUL_BRONZE_JOB_REQUIRED`,
`DEDICATED_DEMO_CATALOG_REQUIRED` hoặc
`PIPELINE_OBJECT_STORAGE_UNAVAILABLE`. Đối chiếu snapshot trong MinIO/PostgreSQL
theo scope `manual-cleaning-cleaning-demo`.

Đây là API đồng bộ phục vụ file demo nhỏ và rehearsal ba profile, chưa phải worker
queue hay pipeline production cho 22 entity. Không gọi đồng thời nhiều request trên
cùng session; việc publish vẫn tuân theo kiểm tra base snapshot của Silver.

## 10. Đối chiếu bằng chứng

```sql
SELECT j.ingestion_job_id,d.domain,j.source_table,j.status,j.stage,
       j.rows_read,j.rows_written,j.error_message,j.output
FROM app.ingestion_job j JOIN app.dataset d ON d.dataset_id=j.dataset_id
WHERE d.dataset_code IN ('DEMO_FLOW_admissions_BRONZE','DEMO_FLOW_academic_BRONZE','DEMO_FLOW_hr_BRONZE')
ORDER BY j.ingestion_job_id DESC;
SELECT run_id,status,base_snapshot,issues FROM app.silver_run
WHERE scope='rehearsal:manual-demo-flow1';
SELECT scope,snapshot_id FROM app.silver_head
WHERE scope='rehearsal:manual-demo-flow1';
```

Reports: `.local/live-demo/reports/*.json`. MinIO Silver: key trong receipt.manifest.key,
dưới silver/rehearsal/ bên trong bucket. Không scan prefix để coi mọi file là published.
Không copy Bronze output sang demo/: --ingestion-job đọc thẳng output worker.

Mỗi lần chạy ghi một report riêng; đường dẫn được in sau dòng `Report:` cuối lệnh.
Không cần mở Parquet trực tiếp trong MinIO Console, dùng inspect để đọc bảng.
Bạn có thể ghi bằng chứng vào bảng này:

| Ca | ingestion_job_id | Bronze status | Silver status | snapshot/head | Đường dẫn report | Đạt? |
|---|---|---|---|---|---|---|
| Admissions đúng | | | | | | |
| Retry Admissions | Cùng job trên | | | | | |
| Academic đúng | | | | | | |
| HR đúng | | | | | | |
| Ngày sai | | | | | | |
| Mã lạ | | | | | | |
| HR cập nhật | | | | | | |
| File không tồn tại | | | Không được chạy | | | |

Đối chiếu ingestion_job_id, source_version, issues, head_before/head_after và receipt
nếu publish thành công. Lỗi kết nối, thiếu thư viện hoặc traceback khác không được tính
là đạt ca lỗi nghiệp vụ chỉ vì tiến trình kết thúc thất bại.

| Ca | Bronze | Silver | Điều kiện đạt |
|---|---|---|---|
| File đúng ba domain | SUCCESS | PUBLISHED | Ba entity đúng kiểu/giá trị |
| Retry cùng job/parent | Giữ job cũ | reused=true | Cùng snapshot |
| Ngày sai | SUCCESS | FAILED | DATE_PARSE_ERROR, head giữ nguyên |
| Mã lạ | SUCCESS | FAILED | SOURCE_ID_UNMAPPED, head giữ nguyên |
| Cập nhật | SUCCESS job mới | PUBLISHED | ID cũ, tên mới, không nhân đôi |
| File thiếu | FAILED | Không được chạy | Chặn trước publisher |

## Giới hạn và sự cố

- 401/403: token/role. 409: job còn hoạt động; GET job và kiểm tra worker.
- 422: ID/source/dataset/layer/config; kiểm tra bảng kết quả seed.
- QUEUED mãi: worker chưa chạy hoặc khác DB. Container Up chưa chứng minh từng job thành công.
- INGESTION_READING_FAILED: kiểm tra mount/path/sheet/config; công thức hoặc ô Excel lỗi có thể bị chặn ngay ở reader Bronze.
- Missing table: migration, không reset volume. Đã có một phần bảng thì đối chiếu schema trước.
- SOURCE_ID_UNMAPPED với file đúng: kiểm tra init-job-crosswalk và catalog IDs.
- ACTIVE_SNAPSHOT_CHANGED: kiểm tra parent; buổi mới dùng session flow2 với --base none lần đầu.
- Chưa lưu đầy đủ config snapshot vào ingestion job: không đổi catalog khi đang test.
- Lỗi trước publisher có thể chưa có silver_run/report; lỗi S3/S4 đi qua publisher có FAILED.
- CSV có fixture/test local; qua worker cần datasource CSV với delimiter/encoding/has_header cùng SourceSystem/Dataset demo tương ứng, không chọn CSV cho datasource EXCEL.
- SQL source xuất snapshot.parquet ở Bronze nhưng handoff chuẩn hóa hiện chỉ nhận CSV/Excel.
- Buổi này chưa chứng minh 22 entity/FK, concurrency live, G1 approval và evaluator 39 luật production.

Kiểm chứng cập nhật: 89 tests đạt cho demo, Bronze storage/execution và Silver publication.
Đã kiểm tra Docker status; chưa apply seed/migration hay chạy buổi API → worker → Silver live này.
