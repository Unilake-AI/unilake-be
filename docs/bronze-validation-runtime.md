# Hướng dẫn thực hành validation đầu vào Bronze

> Dành cho môi trường local Windows/PowerShell đã có Docker Desktop và image của dự án.
> Chạy từ D:\Caps1\UniDataLake_AI. Cập nhật hướng dẫn và kiểm tra môi trường ngày 24/10/2026.
> Đây là hướng dẫn thực hành; các kết quả bên dưới là **kỳ vọng để đối chiếu**, không phải khẳng định buổi test trên máy bạn đã chạy thành công.

### Mốc môi trường đã kiểm tra thực tế ngày 24/10/2026

- Đã bổ sung psycopg cho requirements worker, build và khởi động lại backend/worker bằng image mới.
- Worker in VALIDATION_DEPENDENCIES_OK; backend cũng import được psycopg/openpyxl/pyarrow.
- Đã áp dụng migration 20261022_bronze_validation.sql; xác nhận đủ hai cột JSONB trong DB thật.
- Worker kết nối được PostgreSQL; tài khoản Bronze writer/reader/verifier xác thực được và versioning là Enabled.
- Backend trả HTTP 200 cho /health, /docs, /openapi.json; OpenAPI có validation_policy trong request ingestion.
- PostgreSQL, MinIO, Redis và backend healthy; worker chạy hơn 9 phút, không có lỗi mới trong cửa sổ log 60 giây cuối khi kiểm tra.

Đây là kiểm chứng **môi trường sẵn sàng**, chưa phải kết quả của 16 ca ingestion bên dưới. Nếu môi trường vẫn giữ nguyên trạng thái này, có thể bắt đầu từ **mục 3**; mục 2 dùng để kiểm tra lại khi khởi động lần sau hoặc gặp lỗi.

## 1. Buổi test này trả lời câu hỏi gì?

Bạn sẽ đưa CSV, Excel và dữ liệu PostgreSQL thật qua API → hàng đợi PostgreSQL → ingestion worker → validation → MinIO Bronze.

Cần chứng minh bốn điều:

1. Dữ liệu đọc được, đúng cấu trúc nguồn và trong giới hạn được tiếp nhận.
2. Dữ liệu không đạt policy bị chặn trước bước upload, có báo cáo giải thích.
3. Trường hợp được policy cho phép nhưng cần lưu ý được tiếp nhận với WARNING.
4. Có thể truy vết từ job đến đúng object/version/checksum đã lưu trong Bronze.

**Đặt file vào thư mục chưa làm ingestion tự chạy.** Bạn vẫn phải tạo catalog và gọi API tạo job. Hiện chưa có file watcher tự động tạo job.

**Bronze PASS không có nghĩa dữ liệu đã sạch nghiệp vụ hoặc đã lên Silver.** Ví dụ mã chưa có crosswalk, ngày tháng sai về nghiệp vụ hoặc giá trị danh mục chưa chuẩn vẫn có thể qua Bronze nếu cấu trúc nguồn hợp lệ. Bài này kết thúc ở Bronze; không tự gọi pipeline Silver.

### 1.1. Dự án hiện đã có gì?

| Thành phần | Đã có trong code | Ý nghĩa |
|---|---|---|
| API ingestion | POST /api/v1/ingestion/jobs; GET job và lịch sử | Tiếp nhận yêu cầu, kiểm tra quyền, tạo job bền vững |
| Catalog | source_system, data_source, dataset, data_layer | Xác định nguồn nào, cách kết nối, domain và dataset đích |
| Worker | app.ingestion_worker | Nhận job từ PostgreSQL, đọc nguồn, kiểm tra và ghi Bronze |
| Validation policy | CSV, EXCEL, POSTGRESQL | Quy định parser, cột nguồn và giới hạn |
| Policy snapshot | validation_input trong ingestion_job | Ghi lại policy, fingerprint, location và revision nguồn cho job |
| Validation report | validation_report trong ingestion_job | Ghi kết quả, rule đã chạy, mã lỗi và vị trí lỗi |
| MinIO Bronze | Writer và verifier riêng; versioning, metadata, SHA-256 | Ghi và xác minh đúng phiên bản dữ liệu |
| Tệp mẫu cũ | data/standardization/live-demo | Phục vụ demo Bronze → Silver; không tự động gắn validation_policy |

**Điều kiện bật nhánh mới:** request phải có validation_policy. Job không có trường này vẫn đi luồng legacy và validation_report thường là null. Chưa có cơ chế bắt buộc policy cho toàn bộ hệ thống.

### 1.2. Luồng thực tế

~~~text
Người dùng đăng nhập với ADMIN hoặc DATA_ADMIN
  → POST ingestion job kèm validation_policy
  → API kiểm tra catalog và policy; lưu snapshot vào PostgreSQL
  → HTTP 202, job QUEUED
  → worker nhận job, status RUNNING, stage VALIDATING
  → đọc/kiểm tra nguồn
      FAIL hoặc ERROR:
        lưu report → job FAILED → không gọi upload Bronze
      PASS hoặc WARNING:
        lưu report → WRITING → upload và xác minh MinIO
        → COMMITTING → job SUCCESS, stage COMPLETED
~~~

Với file nhỏ, các stage trung gian có thể chuyển rất nhanh nên GET không nhất thiết nhìn thấy từng stage.

## 2. Những gì bạn cần có trước khi tạo dữ liệu test

Bạn đã chuẩn bị Docker; không cần build lại chỉ để bắt đầu buổi test.

| Cần có | Cách kiểm tra | Khi đạt |
|---|---|---|
| Image backend và worker | docker image ls | Có image tương ứng của dự án |
| PostgreSQL, MinIO, Redis | docker compose ps -a | Các service nền chạy; PostgreSQL/MinIO healthy |
| Khởi tạo bucket | Trạng thái minio-init | Exited (0) là bình thường vì đây là service chạy một lần |
| Backend | http://localhost:8000/docs | Swagger mở được |
| Worker | docker compose logs --tail 50 ingestion-worker | Không crash/restart liên tục |
| Credentials runtime | Ba file .env ở mục 2.2 | File tồn tại và khớp tài khoản MinIO thật |
| Migration | Truy vấn ở mục 2.4 | Có hai cột JSONB validation_input và validation_report |
| Tài khoản ứng dụng | Đăng nhập Swagger | Có role ADMIN hoặc DATA_ADMIN |
| Python local | .venv\Scripts\python.exe | Dùng để tạo fixture, không thay thế worker Docker |

### 2.1. Mở PowerShell và khởi động bằng image có sẵn

~~~powershell
Set-Location D:\Caps1\UniDataLake_AI
$env:PYTHONPATH = Join-Path (Get-Location).Path "backend"
$env:PYTHONIOENCODING = "utf-8"
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

docker compose up -d --no-build backend ingestion-worker
docker compose ps -a
docker compose logs --tail 50 backend ingestion-worker
~~~

Kết quả mong đợi: backend và worker chạy; các dependency được Compose khởi động. Không có bước pip install trong lệnh up này.

Nếu lỗi thiếu env file, làm mục 2.2. Nếu lỗi import psycopg, làm mục 2.3. Chỉ tiếp tục khi các lỗi tiền điều kiện đã được xử lý.

### 2.2. Chỉ khi thiếu file credentials MinIO

Kiểm tra sự tồn tại, không in nội dung secret:

~~~powershell
".env.bronze-accounts.local", ".env.bronze-backend.local",
".env.bronze-worker.local", ".env.silver-backend.local" |
    ForEach-Object {
        [PSCustomObject]@{ File = $_; Exists = Test-Path -LiteralPath $_ }
    }
~~~

- Có file gốc .env.bronze-accounts.local và tài khoản MinIO tương ứng: chạy export-runtime để xuất các file runtime còn thiếu.
- Chưa từng tạo bộ tài khoản: prepare → export-runtime → khởi động MinIO → apply.
- Đã có tài khoản MinIO nhưng mất file secret: khôi phục bộ credentials cũ trước. Không sinh secret mới rồi coi như sẽ khớp tài khoản cũ.

**Chỉ cho môi trường chưa provision tài khoản:**

~~~powershell
.\.venv\Scripts\python.exe -m app.infra.minio.service_accounts prepare
.\.venv\Scripts\python.exe -m app.infra.minio.service_accounts export-runtime
docker compose up -d --no-build postgres minio redis minio-init
.\.venv\Scripts\python.exe -m app.infra.minio.service_accounts apply
docker compose up -d --no-build backend ingestion-worker
~~~

Chạy từng lệnh, dừng nếu một lệnh báo lỗi. export-runtime được chạy trước để Compose có đủ env file; apply mới là bước tạo tài khoản thật trên MinIO. Nếu tài khoản đã tồn tại hoặc credentials khác, không tự xóa/đổi secret để chạy tiếp.

Các file runtime được Git ignore, không commit hay gửi nội dung. Chi tiết: [Tài khoản MinIO](bronze-service-accounts.md).

Bộ demo trong tài liệu này đọc file từ /data, không đọc file nguồn trong bucket MinIO khác nên không cần source-reader bổ sung. Nếu chuyển location sang s3://bucket-nguon/..., cần provision SOURCE_MINIO credentials đúng bucket theo tài liệu trên.

### 2.3. Kiểm tra thư viện validation trong worker

~~~powershell
docker compose exec -T ingestion-worker python -c "import psycopg, openpyxl, pyarrow; print('VALIDATION_DEPENDENCIES_OK')"
~~~

Kết quả mong đợi: VALIDATION_DEPENDENCIES_OK.

**Lỗi đã xác nhận trong image worker cũ:** log báo ModuleNotFoundError: No module named 'psycopg'. backend/requirements-worker.txt đã được bổ sung psycopg[binary]>=3.2 để khớp dependency của validator. input_validator.py import psycopg ngay khi nạp module, nên image cũ thiếu nó sẽ không khởi động được, kể cả khi chỉ test CSV. Cần build lại worker một lần để đưa dependency vào image.

Nếu docker compose exec báo container is restarting, chưa thể chạy lệnh kiểm tra bên trong container. Đọc log từ bên ngoài trước:

~~~powershell
docker compose logs --tail 50 ingestion-worker
~~~

Nếu worker báo ModuleNotFoundError: No module named 'psycopg':

1. Bổ sung dòng dưới vào backend/requirements-worker.txt nếu chưa có:
   ~~~text
   psycopg[binary]>=3.2
   ~~~
2. Build lại **worker** vì đây là thay đổi dependency:
   ~~~powershell
   docker compose --progress plain build ingestion-worker
   docker compose up -d --no-build ingestion-worker
   ~~~
3. Kiểm tra lại import và log.

Chỉ sửa requirements trên máy chưa làm thư viện xuất hiện trong image đã build. Sau khi rebuild, dùng up để thay container bằng image mới; restart đơn thuần vẫn dùng container/image cũ. Không cài thủ công vào container rồi coi đó là cách sửa bền vững.

Nếu log **backend** cũng báo thiếu psycopg, image backend đang cũ dù backend/requirements.txt đã khai báo dependency. Chỉ khi gặp trường hợp này mới build thêm backend:

~~~powershell
docker compose --progress plain build backend
docker compose up -d --no-build backend ingestion-worker
~~~

### 2.4. Kiểm tra migration

~~~powershell
@'
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'app'
  AND table_name = 'ingestion_job'
  AND column_name IN ('validation_input', 'validation_report')
ORDER BY column_name;
'@ | docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
~~~

Kết quả phải có đúng hai dòng, kiểu jsonb.

Nếu thiếu cột, đợi các job đang chạy kết thúc rồi áp dụng:

~~~powershell
docker compose stop backend ingestion-worker
Get-Content -Raw -Encoding utf8 .\infra\postgres\migrations\20261022_bronze_validation.sql |
    docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose up -d --no-build backend ingestion-worker
~~~

Migration này chỉ thêm hai cột nullable, chạy lại được. Nó không khởi tạo toàn bộ database. Nếu app.ingestion_job chưa tồn tại, cần hoàn tất schema nền trước; nếu DB cũ thiếu các cột hàng đợi, đối chiếu migration 20261015_ingestion_execution.sql trong [hướng dẫn ingestion](ingestion-execution.md).

Không dùng down -v để xử lý migration vì sẽ xóa volume dữ liệu.

## 3. Tạo dữ liệu nguồn và catalog dành riêng cho buổi test

### 3.1. Bạn sắp tạo những gì?

Lệnh ở mục 3.2 tạo một session mới với mã ngẫu nhiên:

- Thư mục data/bronze-validation-demo/<session>/inputs: các CSV và Excel.
- Thư mục requests: request JSON đã gắn **ID catalog thật**, không dùng ID minh họa 1/2.
- manifest.json: ghi session, nguồn, dataset, domain và đường dẫn của buổi test; không chứa mật khẩu.
- Ba source_system, ba data_source và ba dataset BRONZE cho Admissions/Academic/HR.
- Một schema PostgreSQL riêng, bảng employee hai dòng và tài khoản chỉ có quyền đọc bảng demo đó.
- Chưa tạo ingestion job, chưa upload object và chưa chạy Silver.

Mỗi lần chạy lại khối chuẩn bị sẽ tạo một session mới, không ghi đè demo cũ. Chỉ cần chạy **một lần cho một buổi test**. Tài khoản PostgreSQL dùng để chuẩn bị cần quyền CREATE SCHEMA, CREATE ROLE và thêm catalog; tài khoản quản trị của PostgreSQL Docker local đáp ứng nếu chưa được thay đổi quyền.

Đường kết nối khác nhau:

| Người kết nối | PostgreSQL | File nguồn |
|---|---|---|
| Python trên Windows | localhost:5434 theo port mapping mặc định | D:\Caps1\UniDataLake_AI\data\... |
| Worker trong Docker | postgres:5432 | /data/... |

Nếu đã đổi port mapping, sửa DB_PORT bên dưới cho đúng. Script chỉ dùng DB_USER/DB_PASSWORD/DB_NAME từ cấu hình local; không in các giá trị bí mật.

### 3.2. Chạy một lần để tạo bộ demo

Kiểm tra Python local:

~~~powershell
.\.venv\Scripts\python.exe -c "import psycopg, openpyxl; print('DEMO_DEPENDENCIES_OK')"
~~~

Nếu thiếu thư viện, chỉ cài phần cần cho bước chuẩn bị trên host:

~~~powershell
.\.venv\Scripts\python.exe -m pip install "psycopg[binary]>=3.2" "openpyxl>=3.1"
~~~

Sau đó chạy nguyên khối sau trong cùng cửa sổ PowerShell. Đây là lệnh tạo fixture test, không phải một bước mới trong pipeline sản phẩm.

~~~powershell
$env:DB_HOST = "localhost"
$env:DB_PORT = "5434"

@'
import copy
import json
import secrets
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from openpyxl import Workbook
from app.core.config import settings

session = uuid4().hex[:12]
root = Path("data/bronze-validation-demo") / session
inputs = root / "inputs"
requests = root / "requests"
inputs.mkdir(parents=True)
requests.mkdir()
(root / "evidence").mkdir()
schema = "bv_src_" + session
reader = "bv_read_" + session
password = secrets.token_hex(24)
location = f"/data/bronze-validation-demo/{session}/inputs"

csv_files = {
    "01-valid.csv": "id,name\n001,Nguyen Van A\n002,Tran Thi B\n",
    "02-duplicate-header.csv": "id,id\n001,Nguyen Van A\n",
    "03-empty.csv": "id,name\n",
    "04-extra-column.csv": "id,name,note\n001,Nguyen Van A,extra\n",
    "05-wrong-width.csv": "id,name\n001,Nguyen Van A,extra\n",
    "06-business-values.csv": "id,name\n999,\n999,\n",
}
for name, value in csv_files.items():
    (inputs / name).write_bytes(value.encode("utf-8"))

for name, formula in (("academic.xlsx", False), ("academic-formula.xlsx", True)):
    book = Workbook()
    sheet = book.active
    sheet.title = "Academic"
    sheet.append(["student_id", "course_name"])
    sheet.append(['="S001"' if formula else "S001", "Data Engineering"])
    sheet.append(["S002", "Database"])
    book.save(inputs / name)
    book.close()

definitions = [
    ("CSV", "admissions", location,
     {"delimiter": ",", "encoding": "utf-8", "has_header": True}),
    ("EXCEL", "academic", location,
     {"sheet_name": "Academic", "header_row": 1, "skip_rows": 0}),
    ("POSTGRESQL", "hr", None,
     {"host": "postgres", "port": 5432, "database": settings.db_name,
      "username": reader, "password": password, "connection_config_version": "demo-v1"}),
]
catalog = {}
with psycopg.connect(host=settings.db_host, port=settings.db_port,
                     dbname=settings.db_name, user=settings.db_user,
                     password=settings.db_password, connect_timeout=10) as conn:
    # Mọi thao tác chuẩn bị DB nằm trong một transaction.
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    conn.execute(sql.SQL(
        "CREATE TABLE {}.employee "
        "(employee_id INTEGER PRIMARY KEY, full_name TEXT, salary NUMERIC(10,2))"
    ).format(sql.Identifier(schema)))
    conn.execute(sql.SQL(
        "INSERT INTO {}.employee VALUES (1, 'Nguyen Van A', 12345.67), "
        "(2, 'Tran Thi B', 23456.78)"
    ).format(sql.Identifier(schema)))
    conn.execute(sql.SQL(
        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE"
    ).format(sql.Identifier(reader), sql.Literal(password)))
    conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
        sql.Identifier(settings.db_name), sql.Identifier(reader)))
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
        sql.Identifier(schema), sql.Identifier(reader)))
    conn.execute(sql.SQL("GRANT SELECT ON {}.employee TO {}").format(
        sql.Identifier(schema), sql.Identifier(reader)))

    conn.execute(
        "INSERT INTO app.data_layer(layer_code, layer_name, storage_type) "
        "VALUES ('BRONZE', 'Bronze', 'MINIO') ON CONFLICT(layer_code) DO NOTHING"
    )
    layer_id = conn.execute(
        "SELECT data_layer_id FROM app.data_layer WHERE layer_code='BRONZE'"
    ).fetchone()[0]
    for kind, domain, source_location, configuration in definitions:
        code = f"BV_{session}_{kind}"
        system_id = conn.execute(
            "INSERT INTO app.source_system(source_code, source_name, source_type) "
            "VALUES (%s,%s,%s) RETURNING source_system_id",
            (code, code, kind),
        ).fetchone()[0]
        source_id = conn.execute(
            "INSERT INTO app.data_source"
            "(source_system_id,source_name,source_type,connection_type,location,"
            "configuration,updated_at) VALUES (%s,%s,%s,%s,%s,%s::jsonb,NOW()) "
            "RETURNING data_source_id",
            (system_id, code, kind, "DATABASE" if kind == "POSTGRESQL" else "FILE",
             source_location, json.dumps(configuration)),
        ).fetchone()[0]
        dataset_id = conn.execute(
            "INSERT INTO app.dataset"
            "(dataset_code,dataset_name,domain,source_system_id,data_layer_id,storage_format) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING dataset_id",
            (code, code, domain, system_id, layer_id,
             "PARQUET" if kind == "POSTGRESQL" else kind),
        ).fetchone()[0]
        catalog[kind] = dict(source_system_id=system_id,
                             data_source_id=source_id, dataset_id=dataset_id, domain=domain)

base = json.loads(Path("docs/examples/bronze-validation-job.json").read_text("utf-8"))

def payload(kind, filename, parser, columns):
    item = copy.deepcopy(base)
    ids = catalog[kind]
    item.update(data_source_id=ids["data_source_id"],
                dataset_id=ids["dataset_id"], source_table=filename)
    item["validation_policy"].update(
        policy_id=f"demo-{session}-{kind.lower()}", version="1",
        source_system_id=ids["source_system_id"],
        data_source_id=ids["data_source_id"], dataset_id=ids["dataset_id"],
        parser=parser, columns=columns)
    return item

csv_request = payload("CSV", "01-valid.csv",
    {"kind": "CSV", "delimiter": ",", "encoding": "utf-8",
     "has_header": True, "quote_char": '"', "skip_rows": 0},
    [{"name": "id", "required": True}, {"name": "name", "required": True}])
cases = {"csv-pass": csv_request}
for label, filename in [
    ("csv-duplicate", "02-duplicate-header.csv"), ("csv-empty-fail", "03-empty.csv"),
    ("csv-extra-fail", "04-extra-column.csv"), ("csv-width-fail", "05-wrong-width.csv"),
    ("csv-business-pass", "06-business-values.csv"), ("csv-missing", "missing.csv"),
]:
    item = copy.deepcopy(csv_request)
    item["source_table"] = filename
    cases[label] = item
for label, original, flag in [
    ("csv-empty-warning", "csv-empty-fail", "allow_empty_records"),
    ("csv-extra-warning", "csv-extra-fail", "allow_extra_columns"),
]:
    item = copy.deepcopy(cases[original])
    item["validation_policy"][flag] = True
    item["validation_policy"]["version"] = "2"
    cases[label] = item
item = copy.deepcopy(csv_request)
item["validation_policy"]["limits"]["max_records"] = 1
item["validation_policy"]["version"] = "limit-1"
cases["csv-limit-fail"] = item
item = copy.deepcopy(csv_request)
item["validation_policy"]["dataset_id"] += 1
cases["policy-binding-rejected"] = item

excel_request = payload("EXCEL", "academic.xlsx",
    {"kind": "EXCEL", "sheet_name": "Academic", "header_row": 1,
     "skip_rows": 0, "formula_policy": "reject"},
    [{"name": "student_id"}, {"name": "course_name"}])
cases["excel-pass"] = excel_request
item = copy.deepcopy(excel_request)
item["source_table"] = "academic-formula.xlsx"
cases["excel-formula-fail"] = item
item = copy.deepcopy(excel_request)
item["validation_policy"]["parser"]["sheet_name"] = "MissingSheet"
cases["excel-sheet-fail"] = item

pg_request = payload("POSTGRESQL", schema + ".employee",
    {"kind": "POSTGRESQL", "schema_name": schema, "table_name": "employee",
     "connection_config_version": "demo-v1"},
    [{"name": "employee_id", "allowed_source_types": ["int4"]},
     {"name": "full_name", "allowed_source_types": ["text"]},
     {"name": "salary", "allowed_source_types": ["numeric"]}])
cases["postgres-pass"] = pg_request
item = copy.deepcopy(pg_request)
item["validation_policy"]["columns"][0]["allowed_source_types"] = ["text"]
cases["postgres-type-fail"] = item

for label, item in cases.items():
    # Mỗi ca có policy_id riêng để dễ truy vết thay đổi.
    item["validation_policy"]["policy_id"] = f"demo-{session}-{label}"
    (requests / f"{label}.json").write_text(
        json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
manifest = dict(session=session, directory=str(root.resolve()), catalog=catalog,
                source_schema=schema, source_reader=reader, worker_location=location,
                cases=sorted(cases))
(root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print("DEMO_DIR=" + str(root.resolve()))
print(json.dumps(catalog, indent=2))
print("Created", len(cases), "request files. No ingestion jobs submitted.")
'@ | .\.venv\Scripts\python.exe -
~~~

Kết quả mong đợi:

- Có dòng DEMO_DIR=...; lưu lại đường dẫn đó.
- In ra ID của ba nguồn CSV/EXCEL/POSTGRESQL.
- Có 16 request JSON.
- Không có mật khẩu trong output/manifest/request JSON. Mật khẩu tài khoản đọc PostgreSQL nằm trong configuration của datasource theo cơ chế catalog hiện tại; không xuất toàn bộ configuration khi chia sẻ log.

Chọn thư mục vừa được in ra:

~~~powershell
$demo = Read-Host "Dan duong dan DEMO_DIR vua duoc in ra, khong them dau ngoac kep"
Get-Content -Raw -Encoding utf8 (Join-Path $demo "manifest.json")
Get-ChildItem (Join-Path $demo "requests")
~~~

Đừng dùng một session khác cho các bước sau.

### 3.3. Kiểm tra worker nhìn thấy file

~~~powershell
$manifest = Get-Content -Raw (Join-Path $demo "manifest.json") | ConvertFrom-Json
docker compose exec -T ingestion-worker ls -l $manifest.worker_location
~~~

Mong đợi: sáu CSV và hai XLSX. Nếu không thấy, kiểm tra mount ./data:/data:ro và đúng session.

**Ý nghĩa của catalog:** ba ID trong policy liên kết quy tắc với đúng nguồn/dataset. Đường /data/... cho worker biết đọc ở đâu. Dataset BRONZE và domain quyết định nơi lưu trên lake. Chưa có dữ liệu nào được ingest chỉ vì đã tạo các bản ghi metadata này.

## 4. Đăng nhập và tạo job CSV đầu tiên

### 4.1. Authorize Swagger

1. Mở http://localhost:8000/docs.
2. Mở POST /api/v1/auth/login → Try it out.
3. Nhập username/password của tài khoản ứng dụng có role ADMIN hoặc DATA_ADMIN.
4. Execute và lấy access_token từ phản hồi thành công.
5. Bấm Authorize ở đầu Swagger, dán token, không có dấu ngoặc kép và không tự thêm chữ Bearer vào ô HTTPBearer.
6. Authorize → Close.

Tài khoản MinIO Console không phải tài khoản đăng nhập API. Đăng nhập thành công cũng chưa tự gắn token vào mọi request Swagger nếu chưa Authorize.

### 4.2. Gửi request CSV hợp lệ

~~~powershell
Get-Content -Raw -Encoding utf8 (Join-Path $demo "requests/csv-pass.json")
~~~

Copy toàn bộ JSON vừa in:

1. Mở POST /api/v1/ingestion/jobs → Try it out.
2. Dán vào request body → Execute.
3. Kết quả đầu tiên mong đợi là **HTTP 202**.
4. Ghi lại ingestion_job_id thật trong **Server response**.
5. Gọi GET /api/v1/ingestion/jobs/{job_id} bằng ID vừa nhận; lặp lại đến SUCCESS hoặc FAILED.

Phần Responses / Example Value ở dưới Swagger chỉ là tài liệu mẫu, không phải kết quả thực thi.

**HTTP 202 mới chứng minh job đã được nhận, chưa chứng minh đã lưu Bronze.**

### 4.3. Kết quả CSV hợp lệ

| Trường GET job | Giá trị mong đợi |
|---|---|
| status | SUCCESS |
| stage | COMPLETED |
| rows_read / rows_written | 2 / 2 |
| validation_report.result | PASS |
| validation_report.can_accept | true |
| validation_report.scan_complete | true |
| validation_report.records_scanned | 2 |
| validation_report.total_errors / total_warnings | 0 / 0 |
| validation_report.policy_fingerprint | Có dạng sha256:... |
| error_message | null |
| output.uri / version_id / sha256 | Có giá trị thực |

Nếu validation_report là null, kiểm tra đã gửi JSON có validation_policy và đang đọc đúng job mới hay chưa.

Không sửa file hoặc catalog trong lúc job đang chạy. File được chụp khi worker thực hiện; việc enqueue chỉ pin location/policy, không giữ sẵn bytes của file ngay lúc bấm Execute.

## 5. Quan sát Bronze trong MinIO và lưu bằng chứng

1. Mở http://localhost:9001 và đăng nhập bằng tài khoản quản trị local của bạn.
2. Chọn bucket Bronze theo cấu hình, mặc định tên bronze.
3. Lấy output.uri của job; bỏ phần s3://bronze/ để có object key.
4. Mở chính key đó. Cấu trúc thường là:
   ~~~text
   admissions/YYYY/MM/DD/job-<id>/01-valid.csv
   ~~~
5. Xem đúng version tương ứng output.version_id; không chỉ chọn version mới nhất.
6. Kiểm tra metadata: sha256, source-system-id, ingestion-timestamp, batch-id.
7. Download đúng version. CSV phải vẫn có mã 001/002 và cùng nội dung file nguồn.

Nếu so sánh file tải về bằng PowerShell, dùng đường dẫn thực của file bạn vừa tải:

~~~powershell
Get-FileHash -Algorithm SHA256 (Join-Path $demo "inputs/01-valid.csv")
$downloaded = Read-Host "Duong dan CSV da tai tu dung version MinIO"
Get-FileHash -Algorithm SHA256 -LiteralPath $downloaded
~~~

Hai hash phải bằng nhau và bằng output.sha256 khi bỏ khác biệt chữ hoa/thường.

Lưu phản hồi GET job thành JSON trong thư mục evidence của session hoặc chụp phần không có secret. Giữ ít nhất: job ID, tên ca test, status, report.result, mã issues, output URI, version và SHA-256.

**Điều đang được chứng minh:** file đã qua validation và được lưu nguyên bytes, có thể truy vết đúng phiên bản. Dữ liệu vẫn ở Bronze, chưa được ánh xạ Unified Schema hoặc chuẩn hóa mã/ngày cho Silver.

## 6. Thử CSV lỗi và cảnh báo

Cách chạy mỗi ca giống mục 4: mở JSON tương ứng trong requests → POST → ghi ID → GET đến trạng thái cuối → kiểm tra report/MinIO.

**Chạy tuần tự.** Đợi job trước kết thúc rồi gửi job tiếp theo của cùng datasource để tránh HTTP 409 SOURCE_ALREADY_RUNNING.

| Request JSON | Chủ đích | Kết quả mong đợi |
|---|---|---|
| csv-pass.json | Header id,name; hai bản ghi | SUCCESS, PASS, 2 dòng |
| csv-duplicate.json | Header id,id | FAILED, FAIL, HEADER_DUPLICATE |
| csv-empty-fail.json | Chỉ có header, cấm dữ liệu rỗng | FAILED, FAIL, EMPTY_SOURCE |
| csv-empty-warning.json | Cùng file rỗng, cho phép rỗng | SUCCESS, WARNING, EMPTY_SOURCE_ALLOWED, 0 dòng |
| csv-extra-fail.json | Có thêm cột note, không cho phép | FAILED, FAIL, SOURCE_SCHEMA_MISMATCH |
| csv-extra-warning.json | Cho phép cột note ngoài contract | SUCCESS, WARNING, EXTRA_COLUMNS_ALLOWED; giữ cả cột note |
| csv-width-fail.json | Header hai cột, một bản ghi ba giá trị | FAILED, FAIL, SOURCE_SCHEMA_MISMATCH |
| csv-business-pass.json | ID 999 lặp lại, tên rỗng | SUCCESS, PASS, 2 dòng; chưa kiểm tra business key/nonnull/crosswalk |
| csv-missing.json | File missing.csv không tồn tại | FAILED, FAIL, SOURCE_NOT_FOUND |
| csv-limit-fail.json | Hai dòng nhưng max_records=1 | FAILED, FAIL, RESOURCE_LIMIT_EXCEEDED |
| policy-binding-rejected.json | Dataset ID bên trong policy cố ý không khớp request | POST 422, job REJECTED, VALIDATION_POLICY_INVALID |

Với lỗi dữ liệu khi worker kiểm tra:

- error_message thường là BRONZE_INPUT_VALIDATION_BLOCKED.
- Mã cụ thể nằm trong validation_report.issues[].code.
- output là null, rows_written là 0.
- Không có object mới ở đường job-<ID lỗi>/ trong Bronze.
- Object của job thành công trước đó vẫn còn.
- scan_complete có thể là false nếu dừng sớm, nhưng có thể true với EMPTY_SOURCE vì đã quét hết; không dùng một giá trị scan_complete cố định cho mọi lỗi.
- rows_rejected hiện không biểu diễn số dòng lỗi, vì nhánh này chặn toàn bộ đầu vào. Không suy ra số lỗi từ trường này.

Với policy sai trước enqueue, lấy ID job REJECTED từ error.details.ingestion_job_id; report có thể null vì worker chưa quét dữ liệu.

WARNING là trường hợp policy chủ động cho phép, không phải hệ thống tự bỏ qua lỗi. Cờ required của columns yêu cầu **có cột**, không có nghĩa mọi ô trong cột bắt buộc khác null/rỗng.

## 7. Thử Excel

Chạy lần lượt ba JSON đã được tạo với source/dataset Excel thật:

| Request | Kiểm tra | Mong đợi |
|---|---|---|
| excel-pass.json | Sheet Academic, hai cột và hai dòng | SUCCESS, PASS, records_scanned=2; Bronze lưu workbook .xlsx nguyên bytes |
| excel-formula-fail.json | Ô A2 có công thức | FAILED, FAIL, EXCEL_FORMULA_UNSUPPORTED |
| excel-sheet-fail.json | Policy yêu cầu MissingSheet | FAILED, FAIL, SHEET_NOT_FOUND |

Đối với file hợp lệ, đối chiếu hash của inputs/academic.xlsx với file tải từ đúng version MinIO tương tự CSV. Không mở rồi Save lại workbook tải về trước khi so hash vì thao tác lưu có thể thay đổi bytes.

Cần hiểu đúng:

- Parser dùng sheet_name=Academic, header_row=1, skip_rows=0, formula_policy=reject.
- Chỉ sheet được cấu hình được kiểm tra; PASS không xác nhận mọi sheet trong workbook.
- Header được trim khi kiểm tra; header rỗng/trùng bị chặn, dữ liệu ở cột không tên bị chặn.
- Dòng trắng được bỏ khỏi số bản ghi và đếm trong blank_rows_skipped.
- Công thức không được tính lại hay thay bằng cached value.
- Hỗ trợ .xlsx/.xlsm; chưa hỗ trợ .xls. Không thực thi macro và không phải bộ quét malware.
- Bytes workbook được lưu nguyên vẹn; validation không xuất workbook đã sửa.

## 8. Thử PostgreSQL thật

Bước chuẩn bị đã tạo một bảng employee trong schema riêng, gồm:

| employee_id (INTEGER) | full_name (TEXT) | salary (NUMERIC(10,2)) |
|---|---|---|
| 1 | Nguyen Van A | 12345.67 |
| 2 | Tran Thi B | 23456.78 |

Datasource dùng postgres:5432 vì worker chạy trong Docker. Tài khoản đọc được cấp quyền trên schema/bảng demo; mật khẩu không có trong policy hoặc report.

### 8.1. Ca hợp lệ

Gửi requests/postgres-pass.json qua Swagger.

Mong đợi:

- HTTP 202 → job SUCCESS.
- validation_report.result=PASS; records_scanned=2.
- output.uri trỏ đến snapshot.parquet dưới domain hr.
- Có version_id và SHA-256.
- Hai bản ghi vẫn đúng giá trị nguồn.

Download đúng version Parquet rồi kiểm tra schema trên host:

~~~powershell
$env:BRONZE_DEMO_PARQUET = Read-Host "Duong dan snapshot.parquet da tai"
.\.venv\Scripts\python.exe -c "import os; import pyarrow.parquet as pq; t=pq.read_table(os.environ['BRONZE_DEMO_PARQUET']); print(t.schema); print(t.to_pylist())"
Remove-Item Env:BRONZE_DEMO_PARQUET
~~~

Nếu host thiếu pyarrow, cài dependency này cho .venv trước khi đọc file, không cần rebuild backend.

Mong đợi: employee_id là int32, full_name là string, salary là decimal128(10, 2); hai giá trị lương đúng. PostgreSQL không có “file gốc” để so bytes: cần so số dòng, giá trị và kiểu. Đây là snapshot dữ liệu nguồn, chưa phải schema Silver.

### 8.2. Ca sai kiểu theo policy

Gửi requests/postgres-type-fail.json. Policy cố ý yêu cầu employee_id có kiểu text trong khi bảng thật là int4.

Mong đợi: FAILED, report FAIL, SOURCE_SCHEMA_MISMATCH, không upload snapshot của job lỗi.

Dùng tên kiểu PostgreSQL nội bộ trong allowed_source_types: int4, text, numeric; không dùng tên alias integer.

### 8.3. Cách xử lý hiện tại

Nhánh có policy dùng psycopg, transaction REPEATABLE READ READ ONLY và server cursor với batch 512 dòng. Schema và các batch thuộc cùng snapshot. Parquet có schema tường minh, không suy kiểu chỉ bằng mẫu đầu.

Datasource phải có updated_at và connection_config_version; parser phải tham chiếu đúng version. Nếu thay cấu hình nguồn khi job đã enqueue, worker có thể chặn job cũ thay vì dùng cấu hình mới một cách âm thầm. Lỗi binding/revision trước lúc quét có thể xuất hiện dưới mã INGESTION_READING_FAILED và không có report đầy đủ.

Luồng PostgreSQL **không có policy** vẫn dùng reader DuckDB cũ. Không sử dụng trigger DuckDB để điều phối validation.

Tập kiểu hiện hỗ trợ: int2/int4/int8, bool, float4/float8, text/varchar/bpchar, bytea, date, timestamp/timestamptz, time, numeric có precision/scale phù hợp Arrow (precision <= 76; 0 <= scale <= precision). UUID, JSON, array, interval, numeric không ràng buộc và kiểu chưa hỗ trợ bị chặn; không tự chuyển thành text.

## 9. Đọc báo cáo và metadata trong PostgreSQL

Chọn một job của buổi test:

~~~powershell
$jobId = [long](Read-Host "Nhap ingestion_job_id can xem")
@"
SELECT ingestion_job_id, status, stage, rows_read, rows_written, error_message,
       validation_report->>'result' AS validation_result,
       validation_report->>'scan_complete' AS scan_complete,
       validation_report->>'policy_fingerprint' AS policy_fingerprint,
       validation_report->'issues' AS issues,
       output->>'uri' AS bronze_uri,
       output->>'version_id' AS bronze_version
FROM app.ingestion_job
WHERE ingestion_job_id = $jobId;
"@ | docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
~~~

Ý nghĩa từng nhóm:

| Nhóm | Chứng minh điều gì? |
|---|---|
| ID catalog | Job dùng đúng nguồn và dataset đích |
| validation_input | Policy/parser/location đã được pin cho job; không lưu password |
| policy_id/version/fingerprint | Truy vết quy tắc thực tế đã áp dụng |
| checks | Rule nào đã chạy, PASS/WARNING/FAIL/ERROR/NOT_RUN |
| issues | Mã lỗi có kiểm soát và tọa độ nếu xác định được; không ghi raw value |
| records_scanned | Số bản ghi đã quét, không luôn bằng tổng đầu vào nếu dừng sớm |
| job SUCCESS + output | Đã công bố kết quả ghi Bronze thành công |
| URI + version + SHA-256 | Đọc lại đúng object và kiểm tra tính toàn vẹn |

**Report PASS chưa đủ để kết luận job thành công.** Nếu upload/xác minh MinIO thất bại, job vẫn FAILED và report PASS được giữ. Ngược lại, nếu không lưu được report vào PostgreSQL, nhánh validation không tiếp tục upload.

PostgreSQL và MinIO không nằm trong cùng một transaction. Lỗi sau upload có thể để lại object chưa được job công bố; chỉ dùng job SUCCESS và version trong output làm bằng chứng publication. Không suy ra thành công từ việc “thấy file trong bucket”.

## 10. Checklist nghiệm thu bằng tay

| Ca | Ghi job ID | Kết quả cần có | Đối chiếu Bronze |
|---|---|---|---|
| CSV hợp lệ | ... | SUCCESS / PASS / 2 dòng | Đúng version, hash bằng nguồn |
| CSV header trùng | ... | FAILED / HEADER_DUPLICATE | Không có object cho job lỗi |
| CSV rỗng cấm nhận | ... | FAILED / EMPTY_SOURCE | Không có upload |
| CSV rỗng được phép | ... | SUCCESS / WARNING / 0 dòng | Có file chỉ header |
| CSV cột thừa được phép | ... | SUCCESS / WARNING | Giữ cả cột thừa |
| CSV vượt giới hạn | ... | FAILED / RESOURCE_LIMIT_EXCEEDED | Không có upload |
| Policy binding sai | ... | REJECTED / VALIDATION_POLICY_INVALID | Worker không quét |
| Excel hợp lệ | ... | SUCCESS / PASS / 2 dòng | Hash workbook bằng nguồn |
| Excel công thức | ... | FAILED / EXCEL_FORMULA_UNSUPPORTED | Không có upload |
| PostgreSQL hợp lệ | ... | SUCCESS / PASS / 2 dòng | Parquet giữ kiểu/giá trị |
| PostgreSQL sai kiểu | ... | FAILED / SOURCE_SCHEMA_MISMATCH | Không có upload |
| Giá trị nghiệp vụ chưa chuẩn | ... | SUCCESS / PASS | Chứng minh ranh giới Bronze, chưa chứng minh Silver |

Buổi test đạt khi các ca trả đúng kết quả kỳ vọng, kể cả các ca được thiết kế để FAILED. “Tất cả job SUCCESS” không phải mục tiêu của bộ test này.

## 11. Chạy test tự động: hiểu đúng phạm vi

### 11.1. Regression không cần PostgreSQL/MinIO thật

Từ repo root, dùng Python local đã cài dependency kiểm thử:

~~~powershell
Push-Location .\backend
try {
    ..\.venv\Scripts\python.exe -m pytest tests/test_bronze_validation_contracts.py tests/test_bronze_input_validation.py tests/test_ingestion_execution.py -q -o "addopts=" -p no:cacheprovider
} finally {
    Pop-Location
}
~~~

Bộ này kiểm tra contract, file CSV/Excel, điều phối và các tình huống lỗi. Một số connector được giả lập; pass không chứng minh Docker hoặc MinIO thật hoạt động. Việc bỏ addopts mặc định ở đây chỉ dành cho lần chạy tập con, không thay thế yêu cầu coverage CI của toàn dự án.

### 11.2. Bộ live có sẵn trong repository

~~~powershell
$previousLiveFlag = $env:INGESTION_LIVE_TEST
Push-Location .\backend
try {
    $env:INGESTION_LIVE_TEST = "1"
    ..\.venv\Scripts\python.exe -m pytest tests/test_ingestion_execution_live.py -q -o "addopts=" -p no:cacheprovider
} finally {
    if ($null -eq $previousLiveFlag) {
        Remove-Item Env:INGESTION_LIVE_TEST -ErrorAction SilentlyContinue
    } else {
        $env:INGESTION_LIVE_TEST = $previousLiveFlag
    }
    Pop-Location
}
~~~

Phải có dependency test trên host, cấu hình DB localhost/port đã publish, MinIO localhost:9000, credentials SA đúng và quyền quản trị local cho fixture tạo/dọn schema/object thử.

Phạm vi thật của bộ này:

- PostgreSQL và MinIO là thật.
- API chạy trong tiến trình pytest qua ASGITransport, xác thực được dependency override.
- Worker được gọi qua run_once trong tiến trình test.
- Không chứng minh request đi qua cổng HTTP 8000 hay process worker Docker đang chạy.
- Có schema và prefix riêng theo UUID, dọn dữ liệu test trong finally; nếu process bị kill thì có thể còn dữ liệu thử.
- Bỏ biến INGESTION_LIVE_TEST sẽ khiến các ca bị skipped; skipped không phải passed.

**Hai bất nhất còn thấy trong file test hiện tại, cần xử lý trước khi dùng kết quả toàn bộ suite làm cổng nghiệm thu:**

1. Ca file CSV bị xóa vẫn assert INGESTION_READING_FAILED dù request đã có policy; nhánh mới dự kiến trả BRONZE_INPUT_VALIDATION_BLOCKED với issue SOURCE_NOT_FOUND.
2. Ca Excel ghép domain academic-validation-<UUID 32 ký tự> dài 52 ký tự, vượt dataset.domain VARCHAR(50), có thể lỗi ngay lúc tạo fixture.

Đây là điểm cần sửa ở test, không phải lý do đổi kết quả mong đợi của sản phẩm. Tài liệu này không tự sửa code/test. Luồng test bằng Swagger + container ở các mục trên dùng fixture khác, không phụ thuộc hai lỗi này.

Không xem các con số test pass trong tài liệu cũ là bằng chứng rằng phiên bản hiện tại đã vượt qua buổi live test trên máy bạn. Hãy lưu output của lần chạy thực tế.

## 12. Lỗi thường gặp và xử lý theo thứ tự

| Hiện tượng | Kiểm tra / cách xử lý |
|---|---|
| Docker Desktop is manually paused | Bấm Resume/Unpause trong Docker Desktop rồi chạy lại lệnh; không cần rebuild vì lỗi này |
| env file ... not found | Mục 2.2; không tạo file rỗng để bỏ qua credentials |
| Worker Restarting / thiếu psycopg | Mục 2.3; dependency worker và backend có danh sách riêng |
| Worker báo cycle failed (gaierror) | Kiểm tra PostgreSQL có đang tắt và hostname postgres có phân giải được trong Docker; khởi động đủ dependency bằng up -d --no-build backend ingestion-worker |
| Worker báo cycle failed (ProgrammingError) | Kiểm tra schema/columns bằng mục 2.4; trong lần kiểm tra thực tế, DB thiếu hai cột validation và hết lỗi sau migration |
| HTTP 401 | Đăng nhập lại và Authorize Swagger bằng access_token |
| HTTP 403 | Tài khoản ứng dụng cần ADMIN/DATA_ADMIN để tạo job |
| HTTP 404 lúc POST | Dùng đúng ID của manifest/session; không dùng 1/2 từ mẫu |
| HTTP 409 | Job cùng datasource đang QUEUED/RUNNING; đợi hoặc kiểm tra worker |
| HTTP 422 VALIDATION_POLICY_INVALID | ID giữa policy/request/catalog, parser.kind, tên bảng và version kết nối |
| Job QUEUED mãi | Worker có chạy không, có nối đúng DB không; xem log |
| SOURCE_NOT_FOUND | File có nằm đúng /data/...; đúng session; bảng PostgreSQL có tồn tại |
| SHEET_NOT_FOUND | Sheet là Academic trong demo mới, không phải Data của bộ demo cũ |
| SOURCE_SCHEMA_MISMATCH | Header, số cột, cột thừa, thứ tự hoặc kiểu PostgreSQL không khớp |
| INGESTION_WRITING_FAILED | Quyền writer/verifier, bucket/versioning, kết nối và checksum; report có thể vẫn PASS |
| SOURCE_ACCESS_DENIED | Quyền đọc nguồn file/MinIO/PostgreSQL; không thay runtime bằng root để lách |
| INGESTION_READING_FAILED trước report | Catalog inactive, revision đổi hoặc lỗi binding/context; xem log đã che secret |
| validation_report = null | Có thể là job legacy, job bị reject sớm hoặc lỗi trước lúc validator chạy |
| Thiếu cột validation_input/report | Chạy migration mục 2.4 trên đúng database |
| Test live bị skipped | Chưa bật INGESTION_LIVE_TEST=1 |
| Dữ liệu demo đã có nhưng không thấy job | Tạo file/catalog không tự enqueue; phải POST job |
| MinIO có object nhưng job FAILED | Không tự coi object là đã publish; đối chiếu job/output/version |

Lệnh lấy log:

~~~powershell
docker compose logs --tail 100 backend ingestion-worker
~~~

Khi gửi lỗi để kiểm tra, gửi job ID, mã lỗi, report, tên ca test và log đã che thông tin bí mật. Không gửi access token, password, các file .env*.local hoặc toàn bộ datasource.configuration.

## 13. Sau buổi test: phần này có ý nghĩa gì với dự án?

Nếu checklist đạt, bạn có bằng chứng rằng **cơ chế tiếp nhận Bronze có policy đã hoạt động với file và DB thật**:

- Cấu trúc không đọc được/không khớp không bị đẩy xuống lake như một job thành công.
- CSV/Excel hợp lệ giữ dữ liệu thô nguyên bytes.
- PostgreSQL tạo snapshot có kiểu dữ liệu rõ ràng.
- Có metadata để giải thích nguồn, policy, lần chạy và phiên bản đã được tiếp nhận.
- Quá trình có xử lý lỗi và phân biệt lỗi nguồn, lỗi policy, lỗi storage.
- Silver có thể dựa vào job đã publish và exact version để đọc đầu vào.

Chưa được chứng minh chỉ bằng buổi test nhỏ này:

- Mọi nguồn của dự án đều bắt buộc validation: hiện vẫn opt-in theo job.
- Quy tắc business quality đầy đủ cho cả ba domain.
- Mapping Unified Schema, chuẩn hóa kiểu/ngày/mã, crosswalk, ghi Silver/Gold.
- Quản trị policy: registry phê duyệt, immutable version; hiện caller khai báo version, fingerprint phản ánh nội dung.
- Benchmark dữ liệu lớn, tính sẵn sàng và hard timeout/hard memory limit.
- Quarantine lưu file bị chặn; hiện chưa có cơ chế này.
- CDC hoặc incremental PostgreSQL; đây là full snapshot.
- Cơ chế tự động theo dõi thư mục và tạo job.

Giới hạn cần giữ trong báo cáo nghiệm thu: memory_limit_bytes là ước tính buffer hàng/batch Python, không phải trần RSS của process. Timeout kiểm tra tại checkpoint và timeout kết nối/câu lệnh PostgreSQL, chưa có supervisor dừng cứng mọi parser/I/O bị treo. CSV còn chịu field-size limit của thư viện csv; không xem fixture nhỏ là benchmark production.

## 14. Dừng buổi test và chạy lần sau

Dừng dịch vụ, giữ container/image/volume:

~~~powershell
docker compose stop
~~~

Lần sau:

~~~powershell
docker compose up -d --no-build backend ingestion-worker
~~~

Chọn lại $demo bằng đường dẫn session đã lưu; dùng lại request JSON. Mỗi lần POST sẽ tạo job mới với ID và object key mới.

Không cần chạy lại prepare/apply credentials, seed session hay build nếu không có thay đổi tương ứng. Chỉ sửa code backend thì có reload; worker cần restart để nạp code mới. Đổi dependency mới cần build lại service đó; đổi env/config chạy thì dùng up để áp dụng.

Giữ manifest, request và evidence để so sánh. Fixture thủ công được giữ lại có chủ đích; không có lệnh xóa hàng loạt trong hướng dẫn này. Khi cần dọn, chỉ dọn đúng session/schema/role/catalog và object của job đã xác định, không xóa cả bucket hoặc volume.

## 15. Các file để đối chiếu implementation

- [Policy/report contract](../backend/app/modules/ingestion/validation.py)
- [Pin policy và kiểm tra binding](../backend/app/modules/ingestion/validation_binding.py)
- [Validator CSV/Excel/PostgreSQL](../backend/app/modules/ingestion/input_validator.py)
- [Điều phối job](../backend/app/modules/ingestion/execution.py)
- [Capture và lưu Bronze](../backend/app/bronze_capture.py)
- [Metadata/hàng đợi](../backend/app/repositories/ingestion_repo.py)
- [Migration validation](../infra/postgres/migrations/20261022_bronze_validation.sql)
- [JSON policy nền](examples/bronze-validation-job.json)
- [Hướng dẫn Bronze → Silver riêng](standardization/manual-live-demo.md)
