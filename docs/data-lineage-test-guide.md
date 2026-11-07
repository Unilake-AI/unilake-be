# Nghiệm thu data lineage từ Source đến Silver

## Phạm vi và quyết định nghiệm thu

| Phần | Hiện trạng trong checkout | Điều kiện để đóng |
|---|---|---|
| UDL-175 – mô hình | Governance định nghĩa asset, layer, version, run, transformation và derivation nhiều input. Source có thể thiếu version. | Đủ contract dataset v1 theo phạm vi ghi trong data-lineage-contract.md. Nếu yêu cầu quan hệ cột hoặc mỗi bước có run riêng thì cần chốt AC riêng. |
| UDL-176 – ghi và đọc bằng chứng | Job SUCCESS lưu output.lineage. Publisher ghi dataset_lineage theo batch trong manifest, reader nội bộ đọc snapshot đã publish và lần theo parent. | Đối chiếu cùng một phiên bản Bronze ở hai phía theo mục 10. Phân biệt kết quả rehearsal và production. |
| UDL-177 / UDL-20 – tra cứu cho người dùng | Chưa có API graph nối hai nguồn metadata, viewer trong checkout này, hoặc đường truy vấn downstream để hỏi ai bị ảnh hưởng. Seed có lineage.read. | Chốt tra cứu theo dataset/version, chiều đi, dữ liệu legacy/missing, giới hạn độ sâu, 403 và lọc theo domain cả ở các node liên quan. Kiểm tra việc ẩn URI nội bộ. |
| Silver → Gold | DTO nhận GOLD nhưng chưa có producer ghi derivation phiên bản Gold đã publish. | Khi có publisher, chứng minh từ một output Gold cụ thể truy ngược về đúng snapshot Silver và nguồn. |
| Điều tra lỗi và tác động ở mức cột | Bảng data_lineage mô hình hóa liên kết cột. Luồng v1 ghi ở mức dataset. Lỗi trước Silver publisher có thể chỉ có report, không có silver_run. | Nếu nghiệp vụ cần trả lời “cột nào đổi” hoặc “attempt nào hỏng ở bước nào”, cần mapping cột có căn cứ và vòng đời attempt từ lúc bắt đầu. |

Căn cứ để kiểm tra lại: docs/data-lineage-contract.md, app/modules/governance/contracts.py, app/modules/ingestion/execution.py, app/modules/pipeline/lineage.py và phương thức read_lineage trong app/modules/pipeline/silver_publication.py. GovernanceService.lineage hiện đọc sự kiện in-memory. Không dùng số dòng của bảng data_lineage để đánh giá UDL-176.

## 0. Bắt đầu trên Windows

Mở PowerShell. Dùng Python 3.11+, Docker Desktop ở chế độ Linux containers và tài khoản ứng dụng ADMIN hoặc DATA_ADMIN cho phần live. Chạy trên DB phát triển. Ghi lại phiên bản code và khởi tạo môi trường.

```powershell
Set-Location D:\Caps1\unilake-be
git rev-parse --short HEAD
if (!(Test-Path .env)) { Copy-Item .env.example .env }
if (!(Test-Path .venv\Scripts\python.exe)) { py -3.11 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ingest-docs]"
.\.venv\Scripts\python.exe -m pytest -o addopts= -v tests/test_lineage_contract.py tests/test_ingestion_execution.py tests/test_silver_publication.py tests/test_manual_cleaning_api.py
```

Đọc dòng tổng kết. Mọi ca phải pass. Lệnh bỏ ngưỡng coverage toàn repo vì đây là bốn suite chọn lọc. Nếu lỗi import, kiểm tra lệnh pip và pytest cùng dùng python.exe trong .venv. Các test parent, staged, thiếu evidence và retry trong test_silver_publication.py kiểm tra những tình huống khó tạo bằng Swagger.

Mở .env và điền DB_USER, DB_PASSWORD, DB_NAME, DB_READONLY_USER/PASSWORD, MINIO_ACCESS_KEY/SECRET_KEY, JWT_SECRET_KEY theo .env.example. Host Python dùng localhost và port công bố thực tế. Nếu dùng volume có sẵn, dùng cấu hình khớp với volume. Chạy docker info; nếu không thấy phần Server thì mở Docker Desktop và chờ engine sẵn sàng.

## 1. File và môi trường

Dùng sáu XLSX trong `data/standardization/live-demo`, mỗi file một dòng, sheet Data.
Giữ nguyên header và text cho mã/ngày/Decimal trong lần đầu.

Kiểm tra file và Python trước:

```powershell
Set-Location D:\Caps1\unilake-be
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
Set-Location D:\Caps1\unilake-be
docker compose up -d postgres minio
docker compose run --rm minio-init
docker compose ps -a
```

PostgreSQL/MinIO healthy, init exit 0. Không down -v. Python host dùng localhost:5434
(hoặc cổng thực tế công bố), MinIO localhost:9000, credentials trong `.env`.
Container dùng postgres:5432 và minio:9000 theo Compose.
Nếu thiếu Python dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ingest-docs]"
```

Nếu runner báo `ModuleNotFoundError: No module named 'psycopg'`, cài driver vào đúng
Python trên Windows rồi chạy lại; không cần build lại Docker:

```powershell
.\.venv\Scripts\python.exe -m pip install "psycopg[binary]>=3.2"
```


**Trước khi bật backend/worker:** với MinIO mới đã healthy và init thành công, chạy các lệnh sau. Với tài khoản dịch vụ đã tồn tại dùng quy trình kiểm tra/xuất lại ở [hướng dẫn tài khoản](bronze-service-accounts.md).

```powershell
.\.venv\Scripts\python.exe -m app.infra.minio.service_accounts prepare
.\.venv\Scripts\python.exe -m app.infra.minio.service_accounts apply
.\.venv\Scripts\python.exe -m app.infra.minio.service_accounts export-runtime
```

Volume cũ cần cả hai cột validation_input và validation_report trong app.ingestion_job. Kiểm tra bằng psql với lệnh \d app.ingestion_job sau migration ingestion ở dưới. Nếu thiếu thì từ PowerShell chạy:

```powershell
docker compose cp infra/postgres/migrations/20261022_bronze_validation.sql postgres:/tmp/validation.sql
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/validation.sql'
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
P5 thay toàn bộ entity được gửi, không append/upsert theo dòng. Muốn retry lần cập nhật, dùng UUID parent ban đầu thay cho active.

Để thử lỗi Bronze, dùng cùng cặp ID HR và POST source_table là 99-does-not-exist.xlsx. GET đúng ID đến FAILED. Lưu error_message và xác nhận output không có derivation. Nếu request bị từ chối trước khi enqueue, chưa kiểm được ca worker đọc file lỗi.

## 10. Chứng minh hai cạnh nối cùng một object

1. Mở psql bằng lệnh ở mục 2. Gõ truy vấn dưới, nhập ID job Admissions thật. Với fixture này kết quả phải là SUCCESS, một derivation, run_ref bằng ingestion:<ID>, input là source:<data_source_id>:01-admissions.xlsx và step là INGESTION_CAPTURE. Trong JSON, output.version, uri và checksum của derivation phải trùng version_id, uri và sha256 của receipt. Source version có thể null khi reader không cung cấp.

```sql
\prompt 'Admissions ingestion_job_id: ' jid
SELECT status, dataset_id, output->>'version_id' AS bronze_version,
       output->>'uri' AS bronze_uri, output->>'sha256' AS bronze_sha256,
       jsonb_pretty(output->'lineage') AS evidence
FROM app.ingestion_job WHERE ingestion_job_id = :'jid'::bigint;
```

2. Nhập UUID snapshot Admissions ở mục 6, kể cả khi session đã có head mới. Phải có đúng một dòng snapshot cùng scope và run đã publish. Lấy key từ manifest_ref của chính dòng này.

```sql
\prompt 'Admissions snapshot UUID: ' sid
SELECT s.snapshot_id, s.scope, r.status, s.manifest_ref
FROM app.silver_snapshot s JOIN app.silver_run r ON r.run_id=s.snapshot_id
WHERE s.snapshot_id=:'sid';
```

3. Trong MinIO Console mở bucket Silver theo cấu hình, tìm đúng key ở bước 2, tải manifest JSON. Nếu manifest_ref có version_id, chọn đúng version. Sau khi tải, chạy Get-FileHash -Algorithm SHA256 trên đường dẫn file vừa tải và đối chiếu Hash với manifest_ref.sha256 (bỏ qua hoa/thường). Sau đó tìm dataset_lineage rồi derivations. Với lần publish Admissions đầu tiên, kiểm từng giá trị sau. Một giá trị khác là lý do dừng nghiệm thu dù cả hai job đều báo thành công.

| Trường manifest | Giá trị cần khớp |
|---|---|
| contract_version, mode, parent_snapshot | dataset-lineage-v1, rehearsal, null |
| inputs[0].asset_id, layer, domain | dataset:<dataset_id Admissions>, BRONZE, admissions |
| inputs[0].producer_run_ref, version, uri, checksum | run_ref ingestion và đúng ba giá trị Bronze ở truy vấn 1 |
| output.layer, version, producer_run_ref | SILVER, UUID ở truy vấn 2, silver:<UUID đó> |
| output.checksum | entities.admission_method.object.sha256 trong chính manifest |
| transformations | SCHEMA_MAPPING → TYPE_CONVERSION → IDENTITY_NORMALIZATION có version. Chỉ đòi các bước CLEAN_* nếu đã chạy cleaning. |

4. Lặp truy vấn 2 với snapshot HR sau cập nhật. Manifest mới trỏ parent cũ, edge position có job/version của file 06; entity Admissions kế thừa giữ nguyên object reference. Đi qua parent đến lần cập nhật Admissions để tìm edge ban đầu. Khi retry mục 6, lưu hai receipt và kiểm cùng snapshot, cùng manifest_ref.
5. Với job Bronze thất bại ở mục 9, thay jid trong truy vấn 1: phải có trạng thái lỗi và không có output lineage thành công. Với lỗi chuyển ngày ở mục 8, lưu report có mã lỗi, so sánh head trước/sau và xác nhận không xuất hiện edge published cho input lỗi. Với snapshot cũ thiếu dataset_lineage, ghi “thiếu bằng chứng”. Unit test missing_updated_lineage kiểm tra reader không mượn nhầm edge của parent.

### Biên bản cho mỗi lượt chạy

| Ca | ID job / phiên bản Bronze | Snapshot / parent / mode | Bằng chứng cần lưu | Đạt / lỗi |
|---|---|---|---|---|
| Admissions mới | | | SQL 1, SQL 2 và manifest khớp | |
| Retry cùng parent | | | Hai receipt cùng UUID | |
| HR update / Admissions kế thừa | | | Hai manifest và object refs | |
| Ngày sai, ID lạ | | | Report, head trước và sau | |
| Bronze lỗi | | | Job lỗi, không có output thành công | |

Đánh giá riêng kết quả pytest và kết quả live. Chỉ đánh dấu đã nghiệm thu Source → Silver khi truy ngược được từ manifest về đúng version và job ở PostgreSQL. API graph, RBAC của graph, viewer và Gold là các mục còn mở. Quyền GET ingestion job hiện trả output chứa lineage và URI. Khi thiết kế viewer cần phân biệt quyền xem job vận hành và quyền xem lineage theo domain.
