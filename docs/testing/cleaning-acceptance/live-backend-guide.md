# Hướng dẫn kiểm thử BE với PostgreSQL/MinIO thật, không dùng FE

Ngày lập: 12/11/2026. Mục tiêu: chạy qua HTTP API → upload/ingestion worker → Bronze → cleaning → quarantine/Silver và kiểm tra governance bằng dịch vụ thật. Đây là live integration trên môi trường bạn đang chạy; chưa tự coi máy local là hệ thống production đã được nghiệm thu.

## 1. Tình trạng đã kiểm tra trực tiếp

| Thành phần | Quan sát |
|---|---|
| BE `http://127.0.0.1:8000/health` | Trả `{"status":"ok"}` |
| OpenAPI `http://127.0.0.1:8000/openapi.json` | Có uploads, cleaning, profiles/approve/revoke, cleaning-runs và quarantine-records |
| `unilake-postgres` | Healthy; host port **5434** → container 5432 |
| `unilake-minio` | Healthy; API 9000, console 9001 |
| `unilake-backend-ingestion-worker-1` | Unhealthy; chạy `python -m app.ingestion_worker`; healthcheck kiểm tra file heartbeat, không phải HTTP |
| DB worker đang kết nối | Thiếu `app.ingestion_worker_heartbeat`, `app.cleaning_run`, `app.silver_source_profile`, `app.silver_profile_revision`, `app.silver_domain_owner`, `app.silver_profile_approval` |
| Crosswalk | Có `app.identity_crosswalk` và `app.crosswalk_release`; chưa kiểm tra nội dung/độ đầy đủ của release |
| Môi trường | Worker là `development`. Settings đọc từ workspace cũng là development; không coi đó là bằng chứng tuyệt đối về biến môi trường của process BE đã chạy từ trước |

Chưa upload dữ liệu, chưa chạy cleaning live, chưa sửa DB hoặc restart dịch vụ trong lượt lập hướng dẫn. `/health` OK không chứng minh schema đã đầy đủ hoặc worker xử lý được job.

## 2. Chuẩn bị môi trường trước khi gửi file lớn

### 2.1. Đúng DB và migration

BE chạy trực tiếp trên Windows phải dùng host `localhost`, port `5434` với cấu hình Docker hiện tại. Worker trong Docker dùng host `postgres`, port `5432`. Không đổi cả hai sang cùng một hostname một cách máy móc.

Backup DB trước migration nếu đang có dữ liệu cần giữ. Không dùng `docker compose down -v` để xử lý lỗi thiếu bảng; thao tác đó có thể xóa volume dữ liệu. Init SQL không tự chạy lại khi volume PostgreSQL đã tồn tại.

Kiểm tra prerequisite: `app.ingestion_job`, `app.dataset`, `app.app_user`, `app.department`, `app.silver_run`, `app.silver_snapshot`, `app.silver_head` và các cột execution/validation đã có. Migration 20261015 tạo Silver/crosswalk không có `IF NOT EXISTS`, không chạy lại toàn bộ thư mục mù quáng.

Với schema nền đã có, các migration mới cần xét theo dependency, đặc biệt **quarantine phải trước data-quality issues**, không đơn giản sắp xếp tên file:

```text
20261103_catalog_metadata.sql
20261106_cleaning_runs.sql
20261106_quarantine_records.sql
20261106_data_quality_issues.sql
20261106_issue_assignees.sql
20261106_pipeline_lineage.sql
20261109_ingestion_uploads.sql
20261110_ingestion_reliability_mvp.sql
20261110_silver_management.sql
20261112_silver_profile_governance.sql
```

Ví dụ chạy **một migration đã xác nhận phù hợp**, tại repository root trong PowerShell:

```powershell
Get-Content -Raw -Encoding UTF8 .\infra\postgres\migrations\20261106_cleaning_runs.sql |
  docker exec -i unilake-postgres sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
if ($LASTEXITCODE -ne 0) { throw 'Migration failed; stop and inspect' }
```

Lặp lại theo thứ tự phù hợp, dừng ngay khi có lỗi. Câu lệnh dùng user/database bên trong container, không cần dán password vào terminal. Xác nhận backup có thể phục hồi trước khi áp dụng trên DB thật có dữ liệu nghiệp vụ.

Sau migration phải có bảng mới, cột `cleaning_run.profile_evidence`, immutable trigger và foreign keys. Current profiles được backfill revision nhưng **không tự approve**.

### 2.2. Bật kiểm soát như production

Để kiểm tra đầy đủ approval gate, đặt `ENVIRONMENT=staging` cho môi trường test bằng dịch vụ thật, hoặc `production` nếu đúng mục đích triển khai. Hai giá trị đều bật gate. `development/local/test` không bật gate bắt buộc này.

Với BE chạy trực tiếp, đặt biến trong terminal khởi động BE rồi restart process. Với Docker, cập nhật cấu hình môi trường và recreate worker; chỉ restart container không tự nạp thay đổi environment từ compose.

```powershell
# Trong terminal dùng để chạy BE, sau khi process cũ đã dừng:
$env:ENVIRONMENT = 'staging'
.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Đặt environment tương ứng trong cấu hình compose trước khi recreate worker. Sau đó kiểm tra `docker ps`, heartbeat và `GET /api/v1/ingestion/workers`. Heartbeat phải tiếp tục tăng khi không có job. Không gửi thêm hàng loạt job nếu worker vẫn unhealthy hoặc heartbeat stale.

### 2.3. Tài khoản và nguồn dữ liệu

Chuẩn bị ADMIN, DATA_ADMIN, MANAGER đúng domain và một STAFF để thử truy cập bị từ chối. Người sửa profile và người duyệt phải khác nhau. Không gửi mật khẩu/token vào report hoặc chat; đăng nhập trực tiếp tại máy test.

Mỗi bộ nguồn cần dataset Bronze ACTIVE, source system và data source khớp domain/source type. Khi dùng upload tạo dataset mới, lấy ID từ response và metadata API/DB được phép truy cập; không dùng ID ví dụ trong unit tests.

Crosswalk release phải tồn tại và có mapping cho ID nguồn/foreign keys cần dùng. Có thể đăng ký bằng công cụ có sẵn, trên đúng DB và với dữ liệu đã được xác nhận:

```powershell
.venv/Scripts/python.exe scripts/standardization/register_crosswalk.py --input .\test-input\crosswalk.json
```

File này có `version`, `entries`; mỗi entry gồm `source_system_id`, `entity`, `source` dạng mảng chuỗi, `evidence`, `valid_from` có timezone; tùy chọn `valid_to`, `canonical_id`. Không lấy ID tự tăng của một DB khác làm canonical ID. Chỉ có file CSV lớn mà không có crosswalk thì nhiều dòng có thể bị quarantine do ID chưa mapping, không phải do khả năng xử lý tải.

## 3. Giới hạn hiện tại và cách chọn kích thước

Các giá trị dưới đây được đọc từ cấu hình worker đang chạy; settings local cũng đang cùng giá trị. Cần kiểm tra lại nếu bạn thay cấu hình hoặc BE dùng environment override riêng.

| Giới hạn | Giá trị |
|---|---:|
| `UPLOAD_MAX_BYTES` | 52,428,800 bytes = **50 MiB** mỗi file |
| `UPLOAD_MAX_RECORDS` | 1,000,000 records |
| `UPLOAD_MAX_COLUMNS` | 500 cột |
| `UPLOAD_MAX_CELL_CHARS` | 1,000,000 ký tự/ô |
| `UPLOAD_TIMEOUT_SECONDS` | 60 giây cho các kiểm tra có áp dụng giới hạn upload; không phải SLA toàn luồng |
| `UPLOAD_MAX_EXPANDED_BYTES` | 209,715,200 bytes = **200 MiB** sau giải nén |

Đạt giới hạn cấu hình không có nghĩa hệ thống đã xử lý ổn định ở kích thước đó. File XLSX nén nhỏ vẫn có thể vượt giới hạn giải nén. Trong code upload còn có budget kiểm tra 256 MiB; đây không phải trần RSS của toàn bộ BE/worker.

Cleaning/publication có thao tác giữ records, issues, lineage và tables trong bộ nhớ. Không giả định luồng này là streaming vô hạn. Lượng crosswalk, parent snapshot và tỷ lệ lỗi cũng ảnh hưởng RAM, thời gian và kích thước report.

Đề xuất tăng tải từng nấc, **chưa phải cam kết năng lực**:

| Nấc | Dữ liệu mỗi entity/nguồn | Mục đích |
|---|---:|---|
| S0 | 100–500 dòng sạch | Kiểm tra mapping, crosswalk, approval và pipeline |
| S1 | 1,000 dòng sạch + bộ file lỗi nhỏ riêng | Xác nhận correctness trước tải |
| L1 | 10,000 dòng sạch | Baseline thời gian/RAM |
| L2 | 50,000 dòng sạch | Tăng tải sau khi L1 đạt |
| L3 | 100,000 dòng sạch | Kiểm tra tăng trưởng bộ nhớ/report/manifest |
| L4 | 250,000 rồi 500,000 dòng | Chỉ chạy khi nấc trước ổn định và file chưa vượt byte limit |
| L5 | Tối đa 1,000,000 dòng | Bài giới hạn tùy chọn, chỉ khi còn trong 50 MiB và tài nguyên phù hợp |

CSV là định dạng ưu tiên cho nấc lớn. XLSX nên bắt đầu 1,000 → 10,000 → 50,000 dòng để đo riêng chi phí đọc/giải nén. Nguồn thực chỉ có ít bản ghi danh mục thì không tạo hàng trăm nghìn chức vụ giả để kết luận tải nghiệp vụ; chọn entity giao dịch có khối lượng thực tế và đủ bảng tham chiếu.

Chuẩn bị file quanh byte/record limit cho ca rejection riêng; không tăng đồng thời mọi giới hạn để ép test xanh. File vượt limit dùng để chứng minh hệ thống từ chối đúng, không phải ứng viên cần publish.

## 4. Chính xác cần chuẩn bị những file nào?

### 4.1. Gói thông tin tối thiểu trước khi tạo dữ liệu lớn

Với **mỗi domain**, cung cấp một nguồn/entity dự kiến release và:

1. CSV UTF-8 hoặc XLSX mẫu sạch 100–500 dòng, header đúng thực tế. Tên file ASCII, không dấu/khoảng trắng: `course.csv`, `position.xlsx`… API hiện giới hạn tên bằng chữ/số/underscore/dot/hyphen và chiều dài tối đa 200.
2. Data dictionary: tên cột nguồn, tên cột đích, kiểu, required/nullable, format, timezone, enum/range, null tokens và alias được chấp nhận.
3. Khóa PK, unique/business/composite; giải thích ý nghĩa từng khóa. Nêu rõ exact duplicates có được collapse hay không.
4. Các bảng parent/reference và sơ đồ FK; thứ tự publish parent trước child. Đừng gửi file child lớn nhưng thiếu parent/crosswalk.
5. Crosswalk release hoặc bảng mapping ID nguồn → canonical ID; namespace/source system, thời gian hiệu lực, nguồn bằng chứng.
6. File là full snapshot hay delta; có được partial không; với dòng lỗi thì có được giữ dòng parent hay không.
7. Quy mô thực tế: số dòng/ngày, kích thước file trung bình/lớn nhất, số cột, số file đồng thời, thời gian hoàn thành mong muốn, RAM/CPU/disk máy test.
8. Người có thẩm quyền xác nhận rule cho mỗi domain. Profile/schema draft vẫn phải đánh dấu cần duyệt.

Không cần tự viết toàn bộ SourceProfile JSON nếu chưa quen: gửi các thông tin trên để dựng profile chính xác. Đừng chỉ gửi file dữ liệu rồi yêu cầu hệ thống tự suy diễn khóa/range/duplicate policy.

### 4.2. Bộ correctness nhỏ cho từng nguồn

Mỗi ca nên là một file riêng có baseline sạch và **một loại biến đổi**, để biết vì sao bị từ chối. Dùng 20–100 dòng là đủ cho negative cases; riêng boundary/tổng hợp có thể dùng 1,000 dòng.

| Nhóm | File/biến thể cần chuẩn bị | Expected cần ghi |
|---|---|---|
| Clean | Tất cả dòng hợp lệ, đủ mapping/parent | Không quarantine; publish được sau approval |
| Required missing | Bỏ giá trị một trường bắt buộc | Locator và code lỗi thiếu giá trị |
| Nullable | Ô trống ở field nullable | Giữ null; không tự điền |
| Null token | Token được khai báo và token chưa khai báo | Xử lý theo từng profile, không suy diễn |
| Layout | Bỏ/đổi tên header, thêm cột ngoài mapping/ignored | Batch reject theo layout |
| Type/format | Chuỗi vào integer; ngày sai format; enum lạ | Quarantine đúng dòng, đúng code |
| Range | Tại min/max và ngay ngoài biên | Chỉ tạo khi schema thật có min/max |
| Exact duplicate | Hai dòng giống nhau, có locator khác | Mặc định quarantine; collapse chỉ khi được duyệt |
| PK conflict | Cùng PK, khác payload | Không chọn winner |
| Business conflict | PK khác nhưng trùng business key | Đúng rule uniqueness đã khai báo |
| Composite conflict | Trùng đủ bộ khóa ghép | Không coi một cột đơn là toàn khóa |
| Cross-stage | Dòng sạch cùng khóa với dòng thiếu/sai trường khác | Partial production bị chặn |
| Unknown key | Dòng lỗi không đủ bằng chứng khóa | Không ngầm cho dòng khác thắng |
| Mixed | 1,000 dòng với khoảng 1%, 5%, 10% lỗi, ghi rõ từng locator | So strict/partial và số liệu đối chiếu |
| References | Thiếu parent, ID chưa mapping, crosswalk sai hiệu lực | Lỗi identity/reference; không bịa mapping |
| Empty/unsupported | File rỗng, định dạng không hỗ trợ | API/ingestion từ chối theo lớp kiểm tra |

Với file lớn, trước hết **100% dữ liệu hợp lệ**, ID/khóa duy nhất thật sự. Không nhân bản 100 dòng thành 100,000 dòng giữ nguyên khóa rồi dùng kết quả đó làm benchmark clean-path: khi đó bạn đang đo duplicate/quarantine path.

### 4.3. Cấu trúc thư mục đề xuất

```text
test-input/
  README.md
  manifest.json
  domains/
    admissions/<entity>/
      schema-notes.md
      source-profile.json         # nếu đã dựng; IDs phải là của môi trường test
      expected-results.json
      S0-clean/<source_name>.csv
      N-required-null/<source_name>.csv
      N-key-conflict/<source_name>.csv
      N-cross-stage/<source_name>.csv
      L1-10k/<source_name>.csv
      L2-50k/<source_name>.csv
      L3-100k/<source_name>.csv
    academic/<entity>/...
    hr/<entity>/...
  references/
  crosswalk.json
```

**Giữ cùng tên file trong các thư mục scenario nếu muốn dùng cùng một profile.** Code bind chính xác `source_table` với tên file/job, cùng data_source_id, dataset_id, source_system_id, domain và source_type. Đổi tên `course.csv` thành `course_100k.csv` có thể làm profile không khớp. Đổi từ CSV sang XLSX cũng cần profile phù hợp và approval lại.

Không thêm cột `expected_error` hoặc `scenario_id` vào data file trừ khi được khai báo ignored/mapped. Lưu expected ở sidecar để không biến chính test data thành lỗi layout.

Manifest mỗi file cần: đường dẫn tương đối, domain/entity, format/parser options, số records không tính header, bytes, SHA-256, scenario, profile/version dự kiến, seed nếu tạo synthetic, parent/crosswalk cần thiết và expected clean/quarantine/collapsed/published/blocked. Expected locator phải theo parser, không mặc định số dòng vật lý của CSV có multiline bằng ordinal record.

Có sẵn [manifest mẫu để điền](D:/Caps1/unilake-be/docs/testing/cleaning-acceptance/test-input-manifest.template.json). Đây là sidecar chuẩn bị dữ liệu, không phải payload gửi trực tiếp vào API profile.

## 5. Chạy chỉ bằng Swagger hoặc PowerShell 7

Swagger: mở `http://127.0.0.1:8000/docs`. Có thể đăng nhập, Authorize và gọi các API mà không cần FE. Nếu giao diện Swagger không tự lưu token từ login, dùng access_token trả về theo cơ chế Bearer của OpenAPI.

Các ví dụ dưới đây dùng **PowerShell 7 (`pwsh`)** vì `Invoke-RestMethod -Form` không dùng theo cách này trong Windows PowerShell 5.1. Không in object login hoặc lưu token vào report.

### 5.1. Đăng nhập DATA_ADMIN

```powershell
$api = 'http://127.0.0.1:8000/api/v1'
$credential = Get-Credential -Message 'Tai khoan DATA_ADMIN cua moi truong test'
$body = @{ username=$credential.UserName; password=$credential.GetNetworkCredential().Password } | ConvertTo-Json
$login = Invoke-RestMethod -Method Post -Uri "$api/auth/login" -ContentType 'application/json' -Body $body
$headers = @{ Authorization = "Bearer $($login.access_token)" }
$body = $null
```

Lặp lại cho ADMIN và owner ở các biến header riêng. ID owner lấy từ tài khoản thật của môi trường; không dùng ID giả trong test code. Khi token hết hạn, đăng nhập/refresh lại thay vì tắt auth.

### 5.2. Upload vào dataset đã chuẩn bị

```powershell
$datasetId = [int](Read-Host 'Dataset Bronze ID da xac nhan')
$filePath = (Resolve-Path '.\test-input\domains\academic\course\S0-clean\course.csv').Path
$form = @{
  file = Get-Item -LiteralPath $filePath
  dataset_id = "$datasetId"
  parser_options = (@{ delimiter=','; encoding='utf-8' } | ConvertTo-Json -Compress)
}
$job = Invoke-RestMethod -Method Post -Uri "$api/ingestion/uploads" -Headers $headers -Form $form
$jobId = $job.ingestion_job_id
```

Với XLSX, parser options dùng `sheet_name`, `header_row`, `skip_rows`. Không truyền `has_header` vào upload parser_options: API hiện không nhận option đó từ client; CSV tự dùng header.

Nếu chưa có dataset, upload hỗ trợ `dataset_name`, `description`, `domain` thay cho `dataset_id`; không gửi đồng thời cả hai nhóm. Tạo một lần rồi dùng dataset_id trả về cho các scenario, tránh tự tạo dataset mới ở mỗi lần test.

### 5.3. Chờ ingestion kết thúc

```powershell
$deadline = (Get-Date).AddMinutes(10) # thoi gian cho cua client, khong phai SLA
do {
  $job = Invoke-RestMethod -Uri "$api/ingestion/jobs/$jobId" -Headers $headers
  if ($job.status -in @('SUCCESS','FAILED','REJECTED')) { break }
  Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)
if ($job.status -ne 'SUCCESS') {
  throw "Ingestion chua SUCCESS: $($job.status). Kiem tra worker/job; khong upload lai mu quang."
}
$job | Select-Object ingestion_job_id,dataset_id,data_source_id,status,rows_read,rows_written,rows_rejected
```

Lưu `output.uri`, version_id, sha256, rows_read, parser_options vào evidence. HTTP 202 là nhận job, không phải kết quả cuối.

### 5.4. Dựng profile theo đúng job và đăng ký crosswalk

SourceProfile cần data_source_id, dataset_id, source_system_id, source_type, source_table, domain, entity, version, scope, source_schema_version, source_schema, contracts, field_mapping, crosswalk_release và effective_at có timezone; thêm null/format/type/dictionary/reference options đúng nguồn.

`source_schema` và `contracts` là nội dung JSON, không chỉ đường dẫn file. Mapping phải đủ field yêu cầu. Contracts cần các entity tham chiếu liên quan. Dùng scope riêng cho test, không trộn vào active scope nghiệp vụ. Không coi JSON trong docs là policy được duyệt chỉ vì có thể load được.

Đăng ký bằng `POST /pipeline/profiles` với `{profile_id, definition}`. Lưu revision và fingerprint trả về. Khi đổi nội dung dùng PUT kèm expected_revision; sau đó phải approve lại.

### 5.5. Preview và approval

```powershell
$profileId = Read-Host 'Profile ID da dang ky'
$preview = Invoke-RestMethod -Method Post -Uri "$api/pipeline/cleaning/ingestion-jobs/$jobId/preview?limit=10" `
  -Headers $headers -ContentType 'application/json' -Body (@{profile_id=$profileId}|ConvertTo-Json)
```

Preview không ghi Silver/quarantine. Nhưng `limit=10` chỉ giới hạn output mẫu, **không làm hệ thống chỉ xử lý 10 dòng**. Không chạy preview file khổng lồ trước khi có baseline.

ADMIN cấp grant bằng `POST /pipeline/profiles/owners/grants` với domain/user_id/reason. Owner đọc revision, rồi `POST /pipeline/profiles/{id}/approve` với expected_revision, expected_fingerprint, grant_id, evidence và allow_partial_publication. Người sửa profile không tự duyệt; DATA_ADMIN không mặc nhiên là owner.

Để test strict và partial trên cùng revision, owner có thể duyệt partial nếu đã đồng ý policy; strict run vẫn gửi false. Để kiểm tra chặn partial chưa được duyệt, dùng một profile/scope test riêng với approval false. Không thu hồi profile đang phục vụ dữ liệu thật chỉ để chạy negative test.

### 5.6. Cleaning — bắt đầu strict

```powershell
$request = @{ profile_id=$profileId; base_snapshot='active'; allow_partial_publication=$false } | ConvertTo-Json
$timer = [System.Diagnostics.Stopwatch]::StartNew()
$result = Invoke-RestMethod -Method Post -Uri "$api/pipeline/cleaning/ingestion-jobs/$jobId" `
  -Headers $headers -ContentType 'application/json' -Body $request -TimeoutSec 600
$timer.Stop()
$runId = $result.run_id
$result | Select-Object run_id,status,records_read,records_passed,records_quarantined,records_collapsed
$timer.Elapsed.TotalSeconds
```

Đây là lời gọi cleaning đồng bộ, không phải enqueue background như upload. Client timeout không chứng minh server đã dừng; tra cleaning-runs trước khi bấm lại. `TimeoutSec 600` là ví dụ client, không tăng giới hạn xử lý server và không cam kết hoàn tất trong 10 phút.

Đối với dữ liệu lỗi, strict có thể trả QUARANTINED và publication BLOCKED: đây là expected nếu đúng profile. Với clean data và đủ parent/crosswalk/approval, cần xác nhận publication thực sự tồn tại.

Sau khi strict đúng, chạy partial bằng `allow_partial_publication=true` trên scope test đã chuẩn bị. Có approval partial vẫn chưa đủ: guard khóa phải PASSED, các kiểm tra schema/identity/reference phải đạt. Nếu key evidence bị BLOCKED, không sửa expected để ép publish.

### 5.7. Xem kết quả và đối chiếu

```powershell
$run = Invoke-RestMethod -Uri "$api/cleaning-runs/$runId" -Headers $headers
$report = Invoke-RestMethod -Uri "$api/cleaning-runs/$runId/report" -Headers $headers
$summary = Invoke-RestMethod -Uri "$api/quarantine-records/summary?cleaning_run_id=$runId" -Headers $headers
$rows = Invoke-RestMethod -Uri "$api/quarantine-records?cleaning_run_id=$runId&limit=100&offset=0" -Headers $headers
$active = Invoke-RestMethod -Uri "$api/pipeline/silver/snapshots/active?profile_id=$profileId" -Headers $headers
```

Phân trang quarantine nếu hơn 100 records. Với từng record cần kiểm tra, dùng `/quarantine-records/{record_id}` để đọc issue/version và `/{record_id}/source` theo quyền/allowlist. Đừng đưa raw PII vào report tải.

Kiểm tra:

- Counts: read = passed + quarantined + collapsed ở kết quả pipeline tương ứng; phân biệt passed với published.
- Error code/locator đúng dòng được gieo lỗi; profile version/fingerprint và approval đúng revision.
- Bronze vẫn cùng version/checksum; không có thao tác sửa dữ liệu gốc.
- Bị chặn thì active head không đổi; file xuất hiện trên MinIO chưa đồng nghĩa đã publish.
- Published thì snapshot/manifest đúng, retained parent rows đúng policy partial.
- Manifest lineage gồm nguồn/version/rule; không thiếu partial_key_check khi partial production.

## 6. Quy trình đo tải không làm lẫn correctness

1. Chạy S0/S1 và negative cases trước. Nếu chưa có một file sạch publish thành công thì chưa chạy 100k dòng.
2. Chạy từng file L1 → L2 → L3 tuần tự, 1 job một thời điểm. Dừng tăng tải khi OOM/restart, heartbeat mất, lỗi checksum, số dòng sai hoặc vượt ngân sách tài nguyên đã thống nhất.
3. Ghi riêng thời gian upload, chờ queue, ingestion và cleaning; không gom thành một số rồi kết luận bottleneck.
4. Theo dõi Docker stats cho worker/PG/MinIO và RSS/CPU của BE Windows. BE không có trong Docker stats nếu chạy trực tiếp.
5. Đo clean path trước; sau đó mixed 1% → 5% → 10% lỗi, vì quarantine/report có chi phí khác. Có thể chặn partial đúng policy; vẫn đo thời gian xử lý và lượng evidence.
6. Chạy lại 3 lần nếu cần độ ổn định; ghi rõ warm/cold và cache/reuse. Reuse snapshot không phải full cleaning/publication benchmark. Để đo full path độc lập cần scope/dataset/base được lập kế hoạch, không tùy tiện đặt base=null khi scope đã có head.
7. Chỉ thử 2 rồi 4 yêu cầu đồng thời sau khi single-run ổn định. Cùng scope có thể gặp CAS `ACTIVE_SNAPSHOT_CHANGED` đúng thiết kế; muốn đo tải độc lập dùng scope test độc lập.

Mẫu dòng kết quả: scenario, file SHA-256, bytes, rows, profile/revision/fingerprint, crosswalk release, job ID, run ID, snapshot ID, queue seconds, ingestion seconds, cleaning seconds, peak RAM BE/worker, error/quarantine/collapse/publish counts, outcome, expected, PASS/FAIL và ghi chú.

Không tự đặt SLA cố định khi chưa biết máy và khối lượng thực. Trước load test cần chốt mục tiêu, ví dụ số file/giờ, thời gian p95, số concurrent requests, RAM tối đa và phần trăm lỗi chấp nhận; phân biệt lỗi hệ thống với dòng bị quarantine đúng rule.

## 7. Nội dung bạn có thể gửi cho bên chuẩn bị dữ liệu

> Cần bộ dữ liệu kiểm thử BE cho admissions, academic và hr. Mỗi domain chọn nguồn/entity thực tế sẽ triển khai. Trước tiên gửi file mẫu sạch 100–500 dòng, data dictionary, PK/business/composite keys, nullable/enum/range/format/null-token rules, parent/reference files và mapping ID/crosswalk có hiệu lực. Cho biết full snapshot hay delta, strict/partial và collapse policy đã được ai duyệt.
>
> Sau khi mẫu nhỏ chạy đúng, tạo các bộ sạch 10k, 50k, 100k dòng; 250k/500k/1M chỉ khi kích thước file và máy cho phép. CSV UTF-8 ưu tiên; mỗi file hiện phải nằm trong 50 MiB và 1M records. ID/khóa phải duy nhất và FK/crosswalk đầy đủ, không nhân bản nguyên khóa để tạo tải giả.
>
> Chuẩn bị riêng các file lỗi nhỏ: required/null, format/type, enum/range, exact duplicates, PK/business/composite conflict, dòng lỗi sớm trùng khóa với dòng sạch, missing reference và mixed error 1%/5%/10%. Mỗi file có expected-results riêng theo locator và manifest rows/bytes/SHA-256/seed. Giữ nguyên basename của file nguồn giữa các scenario bằng cách đặt trong thư mục khác nhau. Không chèn cột expected vào data file. Dùng dữ liệu giả hoặc đã ẩn danh nhất quán, giữ nguyên quan hệ giữa các bảng.

## 8. Để bắt đầu cùng bạn, cần nhận gì trước?

Chưa cần chuẩn bị hàng triệu dòng ngay. Gửi đường dẫn thư mục **mẫu sạch 100–500 dòng của ba nguồn**, tên domain/entity, bản mô tả cột/khóa/quan hệ, policy hiện có và thông tin quy mô/RAM máy. Nếu chỉ sẵn một domain, có thể chạy smoke test domain đó trước rồi mở rộng; chưa ghi nhận đã nghiệm thu đủ ba domain.

Tài khoản đăng nhập dùng trực tiếp ở máy test; không gửi mật khẩu trong chat. Cần xác nhận DB Docker này là môi trường thử nghiệm riêng hay có dữ liệu nghiệp vụ cần bảo vệ trước khi áp dụng migration/chạy các tình huống revoke, conflict, fault injection. Hướng dẫn trên chưa thay thế cho kết quả live PASS/FAIL: kết quả đó chỉ có sau khi các bước được thực thi và lưu evidence.
