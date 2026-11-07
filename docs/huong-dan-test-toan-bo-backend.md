# UniLake backend · Hướng dẫn chạy và nghiệm thu từ đầu trên Windows

Phạm vi: checkout D:\Caps1\unilake-be, commit e51bb58, đối chiếu ngày 03/11/2026. Dùng một cửa sổ PowerShell cho toàn bộ lệnh. Các khối SQL được dán vào psql sau khi mở bằng lệnh chỉ dẫn. Lần đầu nên làm trọn luồng Admissions ở mục 0–6, đối chiếu mục 10, sau đó thử các ca lỗi và bộ regression.

## Dự án đã làm được gì?

| Phân hệ | Có thể kiểm tra trong checkout này | Giới hạn khi kết luận |
|---|---|---|
| API và tài khoản | FastAPI, login/refresh/logout, liệt kê và thu hồi session, kiểm tra role khi chạy ingestion và cleaning | users, dashboard, query-history có router khung, chưa có endpoint nghiệp vụ trong các router này. Checkout chỉ có backend và worker. |
| Catalog và nguồn | API datasource có list/filter/phân trang, detail, create, patch. Governance catalog trả schema theo domain. Có schema Admissions, Academic, HR | Catalog governance lấy từ registry. Catalog trả kết quả chưa chứng minh một dataset có dữ liệu thực. |
| Source → Bronze | Reader CSV, Excel, PostgreSQL. File giữ byte gốc; bảng PostgreSQL xuất Parquet. Queue/lease trong PostgreSQL; worker ghi version MinIO, kiểm tra receipt và lưu trạng thái | Validation đầu vào áp dụng khi request có validation_policy. Ca Excel demo bên dưới dùng luồng mặc định. Redis là service trong Compose; worker claim job ở PostgreSQL. |
| Bronze → Silver | Cleaning xử lý missing, format, duplicate; sau đó mapping schema, chuyển kiểu, chuẩn hóa định danh qua crosswalk/dictionary. Ghi Parquet và manifest rồi publish snapshot | API cleaning dùng catalog tổng hợp DEMO_FLOW và chạy rehearsal. Bronze SUCCESS chưa kích hoạt Silver tự động. Silver nghiệp vụ trên PostgreSQL và worker tự động là thiết kế đề xuất. |
| Lưu trữ và lineage | MinIO giữ object Bronze và bản ghi Silver dạng Parquet. PostgreSQL giữ job, run/snapshot/head; evidence ở output.lineage và dataset_lineage trong manifest. Có reader nội bộ theo parent | Chưa có API graph/viewer toàn tuyến hay publisher Gold thực tế để nghiệm thu. |
| NLQ | Có API nhận câu hỏi, tạo/kiểm tra SQL và gọi executor | Provider mặc định dùng MockLLMClient và EmptyGoldQueryExecutor. Phản hồi rows rỗng là dự kiến cho smoke test. |

**Flow để theo dõi trong một lượt chạy**

1. Đăng nhập lấy access token → chọn nguồn và dataset Bronze cùng domain → POST job. Ghi **ingestion_job_id**.
2. Worker claim job → đọc nguồn → nếu có policy thì validate → ghi Bronze → xác minh version và checksum → job SUCCESS. Ghi **uri, version_id, sha256**.
3. Người vận hành gọi API cleaning hoặc CLI rehearsal theo ID job → đọc đúng bản Bronze → chuẩn hóa → kiểm tra ràng buộc → ghi/đọc lại Parquet và manifest → publish bằng transaction. Ghi **scope, snapshot_id, parent**.
4. Đọc snapshot đã publish trong DB → lấy manifest_ref → mở đúng manifest trong MinIO → so sánh input với receipt Bronze. NLQ demo là nhánh kiểm thử riêng.

## 0. Chuẩn bị máy và cấu hình

### Điểm bắt đầu trên máy hiện tại

Đã kiểm tra ở root: có đủ năm file bên dưới. Ba file runtime có giá trị khớp file tài khoản tổng. Compose đọc được cấu hình. Python host chưa có .venv trong repo; Docker CLI chưa kết nối được Linux engine tại thời điểm kiểm tra.

| File | Thành phần dùng |
|---|---|
| .env | Compose và công cụ Python chạy trên Windows. Chứa cấu hình DB, MinIO quản trị và JWT. |
| .env.bronze-accounts.local | Bộ thông tin tài khoản dịch vụ đã chuẩn bị; công cụ quản trị và bài test IAM dùng file này. |
| .env.bronze-backend.local | Backend đọc object Bronze. |
| .env.bronze-worker.local | Worker ghi và xác minh bản Bronze. |
| .env.silver-backend.local | Backend ghi Silver khi gọi cleaning. |

**Thứ tự cho lần chạy này:** mở Docker Desktop → kiểm tra Server → tạo .venv → bật postgres/minio/redis → chạy minio-init → xác minh tài khoản trên MinIO → kiểm tra schema DB → bật backend/worker. Giữ các file env hiện tại. LLM_API_KEY và LLM_MODEL có thể để trống khi test NLQ mock. Với demo XLSX mount từ thư mục data, không cần tài khoản đọc bucket nguồn riêng.

Cài Git, Python 3.11, Docker Desktop với Linux containers. Mở Docker Desktop, đợi engine hoạt động. Dùng DB phát triển, chuẩn bị tài khoản ứng dụng admin hoặc dataadmin. Máy mới dùng tài khoản seed trong infra/postgres/seed/03-users-and-roles.sql; DB đã dùng trước đây cần mật khẩu hiện hành của DB đó.

```powershell
Set-Location D:\Caps1\unilake-be
git rev-parse --short HEAD
git status --short
py -3.11 --version
docker version
docker compose version
docker compose config --services
git ls-files --eol infra/postgres/init/*.sh docs/data-schema/*schema.json
```

Phải thấy cả Client và Server của Docker, cùng sáu service postgres, minio, minio-init, redis, backend, ingestion-worker. Khi các file ở trên có w/crlf, đổi riêng chúng về LF trước khi tạo volume DB lần đầu hoặc test checksum. Lệnh sau chỉ thay CRLF bằng LF, giữ nội dung khác của file hiện có.

```powershell
py -3.11 -c "from pathlib import Path; fs=[*Path('infra/postgres/init').glob('*.sh'), *Path('docs/data-schema').glob('*_v1.schema.json')]; [(p.write_bytes(b.replace(b'\r\n',b'\n'))) for p in fs if b'\r\n' in (b:=p.read_bytes())]"
git ls-files --eol infra/postgres/init/*.sh docs/data-schema/*schema.json
if (!(Test-Path .venv\Scripts\python.exe)) { py -3.11 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ingest-docs]"
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose khong doc duoc cau hinh. Dung tai day.' }
Get-Item -Force .env,.env.bronze-accounts.local,.env.bronze-backend.local,.env.bronze-worker.local,.env.silver-backend.local | Select-Object Name,Length
.\.venv\Scripts\python.exe -c "import psycopg,openpyxl,pyarrow,minio; print('HOST_DEPS_OK')"
```

**Điều kiện qua bước 0:** có đủ năm dòng tên file, in HOST_DEPS_OK, Docker có mục Server. Lệnh config --quiet thành công sẽ không in nội dung .env. Cổng host theo cấu hình vừa kiểm tra: PostgreSQL localhost:5434, MinIO API http://localhost:9000. Trình duyệt dùng http://localhost:9001 cho MinIO Console và http://localhost:8000/docs cho Swagger sau khi backend khởi động. Trong Docker, backend/worker kết nối postgres:5432 và minio:9000. Tài khoản đăng nhập Swagger là tài khoản ứng dụng seed trong DB.

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
docker compose up -d --wait postgres minio redis
docker compose run --rm minio-init
if ($LASTEXITCODE -ne 0) { throw 'MinIO init that bai. Kiem tra docker compose logs --tail 60 minio' }
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


### 1.1. Xác minh bộ credential hiện có trên MinIO

Nếu dùng lại volume MinIO cũ, thử quyền đang có trước. Chạy ở PowerShell sau khi minio-init exit 0. Test tạo object giả lập riêng để kiểm tra tài khoản Bronze ghi/đọc đúng quyền.

```powershell
$env:BRONZE_IAM_LIVE_TEST='1'
try {
    .\.venv\Scripts\python.exe -m pytest -q -o addopts= tests/test_minio_service_accounts_live.py
    if ($LASTEXITCODE -ne 0) { throw 'IAM Bronze chua dat. Xem nhanh xu ly ben duoi.' }
} finally {
    Remove-Item Env:BRONZE_IAM_LIVE_TEST
}
```

- **Pass:** sang mục 2. Bài này kiểm quyền Bronze. Quyền ghi Silver được thử khi gọi API cleaning với job thật ở mục 5.
- **MinIO vừa khởi tạo, biết chắc chưa cấp bất kỳ tài khoản dịch vụ nào:** dùng file tổng đã có để tạo tài khoản trên server bằng lệnh dưới, rồi chạy lại test. File runtime đã khớp nên bỏ qua prepare và export-runtime.
- **Có tài khoản từ trước, trạng thái chưa rõ hoặc chỉ thiếu một phần:** kiểm tra đúng endpoint/volume, bucket và tài khoản bằng MinIO Console quản trị. Nếu báo AccessDenied, đối chiếu quyền của đúng role theo [hướng dẫn service account](bronze-service-accounts.md). Nếu lỗi kết nối, kiểm tra docker compose ps -a và cổng 9000 trước.

```powershell
# Chỉ cho MinIO mới chưa có các tài khoản trong file tổng
.\.venv\Scripts\python.exe -m app.infra.minio.service_accounts apply
```

## 2. Migration và API/worker

Volume cũ cần migration ingestion sau; migration này chạy lại được. DB mới có trong init:

```powershell
docker compose cp infra/postgres/migrations/20261015_ingestion_execution.sql postgres:/tmp/ingestion.sql
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/ingestion.sql'
```

Tiếp theo kiểm tra hai cột validation trong DBeaver/pgAdmin hoặc psql (lệnh mở psql ngay bên dưới):

```sql
SELECT column_name, data_type FROM information_schema.columns
WHERE table_schema='app' AND table_name='ingestion_job'
AND column_name IN ('validation_input','validation_report') ORDER BY column_name;
```

Đủ hai dòng jsonb thì bỏ qua block migration validation dưới đây. Nếu bảng ingestion_job đã có nhưng thiếu cột, thoát psql bằng \q rồi chạy ở PowerShell và truy vấn lại. Nếu thiếu cả bảng, kiểm tra log init PostgreSQL và schema nền trước.

```powershell
docker compose cp infra/postgres/migrations/20261022_bronze_validation.sql postgres:/tmp/validation.sql
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/validation.sql'
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
docker compose ps -a
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


### Kiểm tra API, đăng nhập và các phân hệ đọc

```powershell
$api = 'http://localhost:8000/api/v1'
Invoke-RestMethod http://localhost:8000/health
(Invoke-RestMethod http://localhost:8000/openapi.json).paths.PSObject.Properties.Name
$cred = Get-Credential -Message 'Tai khoan ung dung UniLake'
$loginBody = @{username=$cred.UserName; password=$cred.GetNetworkCredential().Password} | ConvertTo-Json
$login = Invoke-RestMethod "$api/auth/login" -Method Post -ContentType application/json -Body $loginBody
Remove-Variable loginBody,cred
$auth = @{Authorization="Bearer $($login.access_token)"}
$login.user
Invoke-RestMethod "$api/auth/sessions" -Headers $auth | Select-Object -First 3
Invoke-RestMethod "$api/datasources?page=1&page_size=5" -Headers $auth | Select-Object total,page
Invoke-RestMethod "$api/governance/catalog?domain_id=admissions" | Select-Object -First 2
Invoke-RestMethod "$api/nlq/ask" -Method Post -ContentType application/json -Body '{"question":"How many students?"}'
```

Kỳ vọng: health trả ok; OpenAPI có ingestion/jobs và pipeline/cleaning; login trả user; danh sách nguồn có tổng và trang; catalog trả metadata domain; NLQ trả sql và rows. Nếu login 401, kiểm tra account seed, DB và mật khẩu. Nếu POST ingestion 403, kiểm tra role. Có thể kiểm tra quyền đọc bằng cách gọi GET datasources từ Swagger khi chưa Authorize, phải bị từ chối. Để kiểm tra create/patch datasource, dùng bộ test_datasources ở mục 11 trên dữ liệu test.

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


### Cách chạy bằng PowerShell thay cho thao tác POST/GET Swagger ở mục 4

Chọn một cách gửi job. Nếu đã POST ở Swagger, bỏ phần tạo job và dùng ID vừa nhận. Điền ID của đúng dòng Admissions sau khi seed.

```powershell
$src = [int](Read-Host 'data_source_id Admissions')
$ds = [int](Read-Host 'dataset_id Admissions')
$body = @{data_source_id=$src; dataset_id=$ds; source_table='01-admissions.xlsx'} | ConvertTo-Json
$job = Invoke-RestMethod "$api/ingestion/jobs" -Headers $auth -Method Post -ContentType application/json -Body $body
$admJob = $job.ingestion_job_id
$until = (Get-Date).AddMinutes(3)
do {
    $job = Invoke-RestMethod "$api/ingestion/jobs/$admJob" -Headers $auth
    $job | Select-Object ingestion_job_id,status,stage,rows_written
    if ($job.status -in 'QUEUED','RUNNING') { Start-Sleep 2 }
} while ($job.status -in 'QUEUED','RUNNING' -and (Get-Date) -lt $until)
if ($job.status -ne 'SUCCESS') { throw "Job $admJob status=$($job.status) error=$($job.error_message). Kiem tra docker compose logs --tail 80 ingestion-worker" }
$job.output | ConvertTo-Json -Depth 12
```

### Kiểm tra endpoint cleaning trên session riêng

Chạy sau Bronze SUCCESS. Nếu dùng Swagger ở trên, nhập ID vào biến $admJob trước. Cách này kiểm API cleaning; CLI ở mục 6 dùng một scope riêng để kiểm snapshot/retry/parent.

```powershell
$admJob = [int](Read-Host 'ingestion_job_id Admissions SUCCESS')
$cleanSession = 'api-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$cleanBody = @{session=$cleanSession; base_snapshot=$null} | ConvertTo-Json
$result = Invoke-RestMethod "$api/pipeline/cleaning/ingestion-jobs/$admJob" -Headers $auth -Method Post -ContentType application/json -Body $cleanBody
$result | ConvertTo-Json -Depth 10
```

Với file Admissions hợp lệ: status PUBLISHED, records_quarantined=0, publication.mode=rehearsal, có snapshot_id. Với QUARANTINED, xem issues, số dòng và kiểm tra head; HTTP 200 tự nó chưa đủ. Ghi scope trả về để tra DB. Ví dụ các lệnh ở mục 10 dùng UUID của luồng CLI, có thể lặp lại với UUID của API.

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

## 11. Bộ test, ca âm và cách đọc kết quả

Tại root, dùng cùng python.exe đã cài dependency. Lệnh đầu chạy regression toàn repo theo cấu hình coverage. Các lệnh tiếp theo tách nhóm để khoanh vùng lỗi; -o addopts= chỉ dùng khi chạy subset.

```powershell
$py = '.\.venv\Scripts\python.exe'
& $py -m pytest
& $py -m pytest -q -o addopts= tests/test_auth.py tests/test_datasources.py tests/test_main.py tests/test_module_boundaries.py
& $py -m pytest -q -o addopts= tests/test_csv_reader.py tests/test_excel_reader.py tests/test_postgresql_reader.py tests/test_ingestion_execution.py tests/test_bronze_input_validation.py tests/test_bronze_validation_contracts.py
& $py -m pytest -q -o addopts= tests/test_silver_contracts.py tests/test_lineage_contract.py tests/test_silver_publication.py tests/test_cleaning_pipeline.py tests/test_dataset_schemas.py
& $py -m pytest -q -o addopts= tests/test_manual_cleaning_api.py tests/test_schema_mapping.py tests/test_type_standardization.py tests/test_identity_standardization.py
```

Đợi từng lệnh in dòng tổng kết. Với validation, kiểm ca header trùng, cột thiếu và input hợp lệ qua suite validator. Muốn thử worker đọc nguồn thật, chạy bộ live sau khi DB/MinIO và tài khoản dịch vụ ở mục 1–2 đã sẵn sàng. Bộ live ingestion dùng schema/prefix fixture riêng, có cả luồng validation; ca này khác ca Excel không truyền policy ở mục 4.

```powershell
$env:INGESTION_LIVE_TEST='1'
$env:BRONZE_LIVE_TEST='1'
$env:BRONZE_IAM_LIVE_TEST='1'
try {
    & $py -m pytest -v -o addopts= tests/test_ingestion_execution_live.py tests/test_bronze_storage_live.py tests/test_minio_service_accounts_live.py
} finally {
    Remove-Item Env:INGESTION_LIVE_TEST,Env:BRONZE_LIVE_TEST,Env:BRONZE_IAM_LIVE_TEST
}
```

Các ca skip do cờ live tắt chỉ cho biết chưa chạy. Kết quả kiểm tra khi soạn hướng dẫn: năm suite ở lệnh có test_silver_contracts.py cho 336 passed, 3 failed tại assert x-source-sha256 của cả ba domain. Tính lại trên bytes LF thì cả ba khớp. Chạy lại suite sau bước LF ở mục 0 để ghi nhận trạng thái trên máy bạn. Docker engine chưa hoạt động khi kiểm tra, chưa có kết quả live mới cho checkout này.

## 12. Chẩn đoán, dừng và chạy lại

| Triệu chứng | Kiểm tra tiếp |
|---|---|
| Docker báo dockerDesktopLinuxEngine | Bật Docker Desktop ở chế độ Linux, chờ docker version có Server. |
| Health ok nhưng không login/list được | Xem docker compose logs --tail 100 postgres backend. Kiểm schema app, seed user và thông tin DB. Nếu init lỗi trên volume đã tạo, sửa LF và đối chiếu phần init/seed còn thiếu trước khi chạy tiếp. Giữ volume để bảo toàn dữ liệu. |
| QUEUED quá 3 phút | Xem docker compose ps -a và docker compose logs --tail 100 ingestion-worker. Kiểm DB, worker, đường dẫn /data trong container và credential service account. Dùng GET lại cùng ID. |
| MinIO AccessDenied | Kiểm tra minio-init exit 0, file env runtime đúng dịch vụ, bucket/versioning, sau đó tái tạo container bị ảnh hưởng khi env đã đổi. |
| ACTIVE_SNAPSHOT_CHANGED | Kiểm tra scope/parent. Retry phải dùng cùng parent ban đầu; đổi sang active sẽ thành yêu cầu khác khi head đã tiến. |
| Không thấy endpoint dashboard hoặc graph | Xem danh sách paths từ OpenAPI ở mục 2 và bảng phạm vi đầu tài liệu. |

Sau khi hoàn tất, thử refresh và logout trên chính session test. Đừng dùng session này cho bước kiểm tra tiếp theo sau logout.

```powershell
$refreshBody = @{refresh_token=$login.refresh_token} | ConvertTo-Json
$next = Invoke-RestMethod "$api/auth/refresh" -Method Post -ContentType application/json -Body $refreshBody
$nextAuth = @{Authorization="Bearer $($next.access_token)"}
Invoke-RestMethod "$api/auth/logout" -Headers $nextAuth -Method Post -ContentType application/json -Body (@{refresh_token=$next.refresh_token} | ConvertTo-Json)
docker compose stop
```

Lần sau, mở Docker Desktop, quay lại root rồi chạy:

```powershell
docker compose up -d
docker compose ps -a
Invoke-RestMethod http://localhost:8000/health
```

Đăng nhập lại để lấy token mới. Lưu biên bản gồm commit, kết quả pytest (pass/fail/skip), ID job và receipt Bronze, UUID/scope Silver, kết quả đối chiếu manifest, một ca lỗi không đổi head. Với phần chưa chạy, ghi “chưa kiểm tra”.
