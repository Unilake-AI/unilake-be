# Buổi test thực tế: upload file Bronze và quan sát Silver

> Hướng dẫn cũ cho upload trực tiếp. Sau khi có ingestion API/worker, dùng
> [manual-live-demo.md](manual-live-demo.md) để test Source → Bronze → Silver qua job.

## Mục tiêu và phạm vi

Bạn upload file bằng MinIO Console, chạy lệnh, đọc dữ liệu Silver và xem lỗi.
Runner `scripts/standardization/live_demo.py` dùng PostgreSQL/MinIO thật, chạy S2 → S3 → S4
và P5 ở chế độ rehearsal. File nguồn được đọc theo object version; Silver là Parquet.
Không cần frontend, backend server hoặc Redis cho buổi này.

Runner dùng source/profile giả lập được khai báo sẵn thay vì catalog datasource/API.
Do đó buổi này kiểm tra file → reader/handoff → chuẩn hóa → Silver, chưa kiểm tra đăng ký
datasource, API upload, scheduler, toàn bộ 22 entity/FK, concurrency hoặc 39 luật DQ production.
Upload một file tự nó không kích hoạt pipeline. Phải chạy lệnh `run`.

## 1. Chuẩn bị Docker và Python

Mở PowerShell tại thư mục gốc:

```powershell
Set-Location D:\Caps1\UniDataLake_AI
docker compose up -d postgres minio minio-init
docker compose ps -a
docker compose logs --tail 40 minio-init
```

PostgreSQL/MinIO phải healthy; minio-init hoàn thành exit 0. Không xóa volume.
Nếu chưa có `.venv`, tạo bằng `py -3.11 -m venv .venv`. Nếu thiếu thư viện:

```powershell
.\.venv\Scripts\python.exe -m pip install -e "./backend[dev,ingest-docs]"
```

Kiểm tra `.env`: DB_HOST=localhost, DB_PORT bằng cổng Docker công bố (mặc định 5434),
MINIO_ENDPOINT=localhost:9000, MINIO_SECURE=false. Tài khoản/mật khẩu theo cấu hình của bạn.
Không đặt postgres/minio là hostname khi chạy Python trực tiếp trên Windows.
Runner đọc `.env` gốc dù bạn gọi từ thư mục gốc hay backend.

## 2. Áp dụng migration nếu chưa có

Trong DBeaver/pgAdmin, kết nối database dự án và kiểm tra:

```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'app' AND table_name IN
('canonical_identity','identity_crosswalk','crosswalk_release',
 'silver_run','silver_snapshot','silver_head') ORDER BY table_name;
```

Nếu cả sáu bảng đã có, bỏ qua các lệnh áp dụng dưới đây.
Nếu chưa có, schema `app` phải tồn tại trước. Nếu chỉ có một phần, chỉ áp dụng migration
của nhóm ba bảng chưa có; không chạy lại CREATE TABLE cho nhóm đã tồn tại.

```powershell
docker compose cp infra/postgres/migrations/20261015_s4_crosswalk.sql postgres:/tmp/s4.sql
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/s4.sql'
docker compose cp infra/postgres/migrations/20261015_p5_silver.sql postgres:/tmp/p5.sql
docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/p5.sql'
```

Các migration hiện không tự chạy khi khởi động Compose và không có IF NOT EXISTS.
Không dùng down -v để giải quyết thiếu bảng. Nếu thiếu schema app, cần sửa khởi tạo DB trước.

## 3. Kiểm tra kết nối và khởi tạo crosswalk demo

```powershell
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py check
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py init-crosswalk
```

`check` phải trả `missing_tables: []`, hai bucket true, `bronze_versioning: Enabled`.
Nếu chưa bật versioning, chạy lại minio-init theo cấu hình Compose hoặc bật trong quản trị
MinIO trước khi upload. File upload trước khi bật versioning cần upload lại.

`init-crosswalk` tạo ba đối chiếu cho mã 007/008/009 trong namespace DEMO_live_*, cấp ID bằng
PostgreSQL và lưu release immutable `manual-live-demo-crosswalk-v1`. Chạy lại sẽ đọc release cũ.
Release đóng băng toàn bộ registry hiện có theo hành vi repository, nhưng runner chỉ dùng các
namespace DEMO_live_*. Không đưa release/report này lên nơi công khai nếu DB chứa metadata thật.

## 4. Upload sáu file Excel vào Bronze

1. Mở http://localhost:9001 và đăng nhập bằng tài khoản MinIO trong `.env`.
2. Mở bucket Bronze (mặc định `bronze`).
3. Tạo/mở prefix `demo/buoi1/`.
4. Upload sáu file `.xlsx` trong `data/standardization/live-demo/`.
5. Kiểm tra object key đầy đủ, ví dụ `demo/buoi1/01-admissions.xlsx`.

Chưa chạy pipeline thì Silver chưa thay đổi. Từng file có một dòng dữ liệu, sheet Data.
Giữ nguyên tên cột, sheet, định dạng text trong lần thử đầu.

## 5. Chạy file tuyển sinh, rồi retry ngay

```powershell
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py run --session buoi1 --domain admissions --object demo/buoi1/01-admissions.xlsx --base none
```

Kỳ vọng status=PUBLISHED, input_rows=1, accepted_rows=1, rejected_locators=[], issues=[].
`tables.admission_method` chứa ID canonical đã cấp, max_score hiển thị "30.00", is_active=true.
JSON trình bày Decimal thành chuỗi; Parquet vẫn lưu kiểu Decimal. Trường description không vào Silver.
Ghi lại `receipt.snapshot_id`, `source_version`, `receipt.manifest.key` và đường dẫn report.

Chạy lại nguyên lệnh trên, KHÔNG upload lại file giữa hai lần. Kỳ vọng `receipt.reused=true`,
cùng snapshot_id. `--base none` giữ nguyên parent của yêu cầu đầu để kiểm tra idempotency.

## 6. Thêm Academic và HR vào snapshot

```powershell
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py run --session buoi1 --domain academic --object demo/buoi1/02-academic.xlsx --base active
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py run --session buoi1 --domain hr --object demo/buoi1/03-hr.xlsx --base active
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py inspect --session buoi1
```

Snapshot cuối có admission_method, semester và position, mỗi entity một dòng.
Semester có start_date=2026-09-01, end_date=2027-01-15, status=Ongoing.
Position có position_level=Staff. Bronze vẫn giữ nguyên các chuỗi tiếng Việt trong file nguồn.
`inspect` đọc snapshot active qua metadata và đọc Parquet thật từ MinIO.

## 7. Thử file sai ngày và mã chưa biết

```powershell
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py run --session buoi1 --domain academic --object demo/buoi1/04-bad-date.xlsx --base active
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py run --session buoi1 --domain hr --object demo/buoi1/05-unknown-id.xlsx --base active
```

Đây là ca lỗi mong đợi: exit code 2, status=FAILED, accepted_rows=0.
Ca ngày có DATE_PARSE_ERROR; ca ID có SOURCE_ID_UNMAPPED. Xem field/locator trong issues.
`head_before` phải bằng `head_after`; chạy inspect lại vẫn thấy ba entity hợp lệ cũ.
File sai vẫn ở Bronze. Không có cơ chế tự đưa dòng sai vào bucket quarantine trong demo này.

## 8. Cập nhật HR

```powershell
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py run --session buoi1 --domain hr --object demo/buoi1/06-hr-updated.xlsx --base active
.\.venv\Scripts\python.exe scripts/standardization/live_demo.py inspect --session buoi1
```

Kỳ vọng position_name = "Giảng viên giả lập cập nhật", ID không đổi, position vẫn một dòng.
Hai entity còn lại giữ nguyên. Snapshot active thay đổi; snapshot cũ vẫn được lưu.
Đây là full replacement entity, không phải append/upsert tự động.

Để thử versioning trên cùng object: upload file cập nhật dưới tên object `03-hr.xlsx` sau
khi đã ghi lại version cũ, rồi chạy lại object đó với --base active. `source_version` phải đổi.
Dùng --version VERSION_ID để đọc chính xác phiên bản cũ; nên dùng session mới khi replay để
không chủ động thay dữ liệu active của buổi demo hiện tại về bản cũ.

## 9. Xem kết quả ở MinIO và PostgreSQL

MinIO Bronze: giữ sáu file nguồn. MinIO Silver: xem object key trong `receipt.manifest.key`.
Artifact rehearsal nằm dưới prefix `silver/rehearsal/` bên trong bucket Silver; prefix này
có thể trùng từ "silver" với tên bucket, đây là cách storage hiện tổ chức key.
Không yêu cầu MinIO Console đọc trực tiếp Parquet; dùng lệnh inspect ở trên để xem bảng.

```sql
SELECT run_id, scope, status, base_snapshot, issues
FROM app.silver_run WHERE scope = 'rehearsal:manual-demo-buoi1';
SELECT snapshot_id, scope, request_hash
FROM app.silver_snapshot WHERE scope = 'rehearsal:manual-demo-buoi1';
SELECT scope, snapshot_id
FROM app.silver_head WHERE scope = 'rehearsal:manual-demo-buoi1';
SELECT namespace, entity, source_key, canonical_id
FROM app.identity_crosswalk WHERE namespace LIKE 'DEMO_live_%';
```

Reports từng lần chạy: `.local/live-demo/reports/*.json`. Lỗi cấu hình, mất kết nối hoặc lỗi
reader trước khi vào publisher có thể dừng bằng traceback và chưa có silver_run/report.
Lỗi chuẩn hóa trong hai file cố ý sai đi qua publisher và ghi FAILED như mô tả.

## 10. Chạy CSV và ghi nhận kết quả

Upload sáu CSV vào `demo/buoi2/`; lặp bước 5–8 với --session buoi2, prefix buoi2 và đuôi .csv.
Lần đầu --base none, các lần cập nhật --base active. Session mới tách snapshot khỏi buổi XLSX.
Muốn retry một lần cập nhật, dùng UUID `base_snapshot` trong report trước, không dùng active
vì active đã đổi sau lần thành công. Không upload lại giữa hai lần retry.

Checklist nghiệm thu buổi demo:

- [ ] Ba domain chạy đúng với XLSX và CSV.
- [ ] Bronze giữ dữ liệu nguồn và object version.
- [ ] Silver đổi đúng tên trường, ngày/Decimal/boolean, ID và dictionary.
- [ ] description không rò sang Silver.
- [ ] Sai ngày/mã chưa biết có lỗi và locator, không thay head.
- [ ] Retry cùng yêu cầu reused=true và không đổi snapshot.
- [ ] File cập nhật giữ ID và thay tên, không nhân đôi dòng.
- [ ] Có report, metadata và Parquet đọc lại được.

## Trạng thái kiểm chứng khi bàn giao

Đã kiểm tra các file qua reader thật và S2–S4, ghi/đọc Parquet local với SQLite metadata,
bao gồm lỗi, retry và cập nhật. Chưa chạy live trên Docker trong lượt chuẩn bị này.
Production vẫn chưa kích hoạt; bộ demo không thay thế G1 approval/39-rule DQ gate.
