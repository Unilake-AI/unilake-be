# Ingestion Source Readers

Tài liệu này ghi lại phần đã triển khai cho lớp đọc dữ liệu nguồn của module ingestion. Mục tiêu là biến một `DataSource` đã đăng ký trong database thành dữ liệu thô có cấu trúc chung để các bước ingestion tiếp theo có thể xử lý, ghi Bronze, validate hoặc chuyển đổi domain.

## Mục Tiêu

Hệ thống cần đọc được nhiều loại nguồn dữ liệu khác nhau nhưng vẫn trả về một định dạng thống nhất. Các nguồn hiện đã hỗ trợ gồm:

- CSV file local.
- Excel file local (`.xlsx`, `.xlsm`).
- PostgreSQL table.
- CSV/Excel nằm trên MinIO/S3 với location dạng `s3://bucket/path/file`.

Các reader không ghi dữ liệu xuống Bronze, không tạo ingestion job và không cập nhật trạng thái ingestion. Chúng chỉ đọc dữ liệu nguồn và trả về `ReadResult`.

## Contract Chung

File chính: `backend/app/modules/ingestion/readers/contract.py`

Các thành phần:

- `ReadRequest`: request chuẩn truyền vào reader.
- `ReadResult`: kết quả chuẩn trả ra từ reader.
- `SourceReader`: protocol để các reader cụ thể implement cùng chữ ký hàm `read()`.

`ReadRequest` gồm:

- `source_type`: loại nguồn, ví dụ `CSV`, `EXCEL`, `POSTGRESQL`.
- `location`: đường dẫn file, thư mục, URL hoặc endpoint tùy loại nguồn.
- `configuration`: cấu hình kỹ thuật theo từng nguồn.
- `source_table`: tên file, sheet/table hoặc bảng nguồn cần đọc.

`ReadResult` gồm:

- `columns`: danh sách tên cột.
- `rows`: dữ liệu dạng list tuple.
- `source_table`: tên nguồn thực tế dùng để truy vết.
- `rows_read`: property trả số dòng đã đọc.

## Các Module Đã Triển Khai

### CSVReader

File: `backend/app/modules/ingestion/readers/csv_reader.py`

Chức năng:

- Đọc CSV local bằng DuckDB.
- Hỗ trợ `location` là file trực tiếp hoặc thư mục chứa file.
- Validate `source_type`, `location`, `delimiter`, `encoding`, `has_header`, `quote_char`, `skip_rows`.
- Giữ dữ liệu thô bằng `all_varchar=True`, phù hợp với tầng Bronze.

Luồng xử lý:

1. Kiểm tra `source_type` là `CSV`.
2. Kiểm tra `location` không rỗng.
3. Lấy cấu hình parser từ `request.configuration`.
4. Validate delimiter, encoding, header và optional config.
5. Resolve đường dẫn file local.
6. Dùng DuckDB `read_csv()` để đọc dữ liệu.
7. Trả về `ReadResult`.

### ExcelReader

File: `backend/app/modules/ingestion/readers/excel_reader.py`

Chức năng:

- Đọc Excel local bằng `openpyxl`.
- Hỗ trợ `.xlsx` và `.xlsm`.
- Hỗ trợ `sheet_name`, `header_row`, `skip_rows`.
- Bỏ dòng dữ liệu hoàn toàn trống.
- Validate header rỗng hoặc trùng tên cột.

Luồng xử lý:

1. Kiểm tra `source_type` là `EXCEL`.
2. Kiểm tra `location`.
3. Validate `sheet_name`, `header_row`, `skip_rows`.
4. Resolve file local từ `location`.
5. Mở workbook bằng `openpyxl.load_workbook(read_only=True, data_only=True)`.
6. Tìm sheet cần đọc.
7. Bỏ qua metadata rows nếu có.
8. Đọc header và chuẩn hóa columns.
9. Đọc rows theo số cột header.
10. Trả về `ReadResult`.

### PostgreSQLReader

File: `backend/app/modules/ingestion/readers/postgresql_reader.py`

Chức năng:

- Đọc bảng PostgreSQL qua DuckDB postgres extension.
- Hỗ trợ `source_table` dạng `table` hoặc `schema.table`.
- Validate identifier để tránh SQL injection.
- Attach PostgreSQL ở chế độ `READ_ONLY`.

Luồng xử lý:

1. Kiểm tra `source_type` thuộc `POSTGRESQL` hoặc alias `POSTGRES`.
2. Validate config kết nối: `host`, `port`, `database`, `username`, `password`.
3. Validate `source_table`.
4. Parse `schema.table`.
5. Quote schema/table an toàn.
6. Load DuckDB postgres extension.
7. Tạo DuckDB secret tạm cho kết nối PostgreSQL.
8. Attach database nguồn read-only.
9. `SELECT *` từ bảng nguồn.
10. Trả về `ReadResult`.

### SourceReaderFactory

File: `backend/app/modules/ingestion/readers/factory.py`

Chức năng:

- Chọn reader phù hợp dựa trên `source_type`.
- Normalize chữ hoa/thường và khoảng trắng.
- Hỗ trợ alias `POSTGRES -> POSTGRESQL`.

Ví dụ:

```python
reader = SourceReaderFactory.get_reader("csv")
```

Kết quả là instance của `CSVReader`.

### Request Builder

File: `backend/app/modules/ingestion/readers/request_builder.py`

Chức năng:

- Chuyển `DataSource` ORM/catalog thành `ReadRequest`.
- Validate `DataSource.is_active`.
- Normalize `source_type`.
- Validate `source_table`, `configuration`, `location`.
- Copy `configuration` để reader không mutate ORM object gốc.

Vai trò của file này là tách phần metadata/catalog ra khỏi reader. Reader chỉ cần biết request chuẩn, không cần biết ORM model.

### MinIO File Resolver

File: `backend/app/modules/ingestion/readers/minio_file_resolver.py`

Chức năng:

- Nhận `ReadRequest` có `location` dạng `s3://bucket/path/file.csv`.
- Tải object từ MinIO/S3 về file tạm local.
- Trả ra một `ReadRequest` mới có `location` là path local.
- Tự xóa file tạm sau khi reader đọc xong.

Lý do cần resolver:

- `CSVReader` và `ExcelReader` hiện chỉ đọc file local.
- Trong database, DataSource ID 1 và 2 đang trỏ đến MinIO:
  - `s3://unilake-raw/admissions/2026/applicants.csv`
  - `s3://unilake-raw/admissions/2026/applicants.xlsx`

Resolver đứng giữa `source_loader` và reader để chuyển remote file thành local file tạm.

### Source Loader

File: `backend/app/modules/ingestion/readers/source_loader.py`

Chức năng:

- Orchestrate toàn bộ flow đọc dữ liệu nguồn.
- Nhận `DataSourceRepository`, `data_source_id`, `source_table`.
- Lấy DataSource từ database.
- Build `ReadRequest`.
- Kiểm tra SourceSystem còn active.
- Chọn reader.
- Resolve MinIO file nếu cần.
- Chạy reader trong worker thread bằng `asyncio.to_thread()`.

`source_loader` không ghi Bronze, không cập nhật ingestion status và không tạo ingestion job.

## Flow Chạy Tổng Thể

Ví dụ caller gọi:

```python
result = await read_data_source(
    repo,
    data_source_id=1,
    source_table="applicants.csv",
)
```

Flow:

1. `read_data_source()` validate `data_source_id`.
2. Gọi `repo.get_with_source_system(data_source_id)`.
3. Nếu không tìm thấy DataSource thì raise `LookupError`.
4. Gọi `build_read_request(data_source, source_table=...)`.
5. Builder validate DataSource active, source_type, configuration, location.
6. Gọi `repo.check_exist_source_system(data_source.source_system_id)`.
7. Nếu SourceSystem không active thì raise `ValueError`.
8. Gọi `SourceReaderFactory.get_reader(request.source_type)`.
9. Gọi `resolve_file_request(request)`.
10. Nếu request là `s3://...`, resolver tải file từ MinIO về file tạm.
11. Reader đọc file local hoặc PostgreSQL table.
12. Trả về `ReadResult`.

Flow dạng rút gọn:

```text
DataSourceRepository
        |
        v
DataSource ORM
        |
        v
build_read_request()
        |
        v
ReadRequest
        |
        v
SourceReaderFactory
        |
        v
resolve_file_request()
        |
        v
CSVReader / ExcelReader / PostgreSQLReader
        |
        v
ReadResult
```

## MinIO Flow Chi Tiết

Với DataSource CSV:

```text
location = s3://unilake-raw/admissions/2026/applicants.csv
source_table = applicants.csv
```

Flow:

1. `resolve_file_request()` phát hiện `location` có scheme `s3`.
2. Parse URI:
   - bucket: `unilake-raw`
   - object key: `admissions/2026/applicants.csv`
   - filename: `applicants.csv`
3. Kiểm tra `source_table == filename`.
4. Tạo MinIO client từ `MinioClientSettings`.
5. Tải object bằng `client.fget_object(...)`.
6. Tạo request mới bằng `dataclasses.replace(...)`.
7. Đổi `location` sang file tạm.
8. CSV/Excel reader đọc file tạm.
9. Context manager kết thúc thì file tạm bị xóa.

## Cấu Hình MinIO

MinIO settings lấy từ:

- `backend/app/core/config.py`
- `backend/app/infra/minio/client.py`
- `docker-compose.yml`

Giá trị mặc định:

```text
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_SECURE=false
```

Console:

```text
http://localhost:9001
```

## Vấn Đề Đã Xử Lý

### 1. CSV/Excel chỉ đọc file local nhưng DataSource trỏ tới MinIO

Vấn đề:

```text
CSVReader hiện chỉ hỗ trợ file local
```

Nguyên nhân:

- DataSource trong database dùng `s3://...`.
- CSV/Excel reader chỉ đọc local path.

Giải pháp:

- Thêm `minio_file_resolver.py`.
- Nối resolver vào `source_loader.py`.
- Trước khi gọi reader, nếu location là `s3://...` thì tải file về local tạm.

### 2. SQLAlchemy không resolve được relationship User

Vấn đề:

```text
expression 'User' failed to locate a name ('User')
```

Nguyên nhân:

- `DataSource` có relationship `"User"`.
- Khi query `DataSource` qua repository, model `User` chưa được import runtime.

Giải pháp:

- Import `User` trong `backend/app/repositories/datasource_repo.py` để SQLAlchemy registry biết class này.

### 3. Integration test lỗi asyncpg khác event loop

Vấn đề:

```text
Future attached to a different loop
```

Nguyên nhân:

- `pytest-asyncio` tạo event loop riêng cho từng parametrized test case.
- SQLAlchemy async engine giữ connection pool từ case trước.

Giải pháp:

- Trong integration test, gọi `await engine.dispose()` sau mỗi case.

### 4. Ruff PT011 với pytest.raises(ValueError)

Vấn đề:

```text
pytest.raises(ValueError) is too broad
```

Giải pháp:

- Thêm `match=...` để test kiểm tra đúng message lỗi.

### 5. Mypy thiếu stub cho thư viện bên thứ ba

Vấn đề:

```text
Cannot find implementation or library stub for module named "duckdb"
Library stubs not installed for "openpyxl"
Cannot find implementation or library stub for module named "minio"
```

Giải pháp:

- Thêm mypy overrides trong `backend/pyproject.toml`:
  - `duckdb`
  - `minio`
  - `openpyxl`

### 6. Mypy không hiểu kwargs dict của DuckDB

Vấn đề:

```text
Argument 2 to "read_csv" has incompatible type "**dict[str, object]"
```

Nguyên nhân:

- Tạo `read_options` dạng dict rồi unpack vào `conn.read_csv(...)`.
- Mypy không suy luận được overload của DuckDB.

Giải pháp:

- Gọi `conn.read_csv(...)` bằng tham số explicit.

### 7. Pre-commit chạy trên staged snapshot cũ

Vấn đề:

- Working tree đã sửa nhưng chưa `git add`.
- Pre-commit stash unstaged changes trước khi chạy hook.
- Hook vẫn thấy code cũ.

Giải pháp:

- Stage lại các file đã sửa bằng `git add`.
- Chạy lại `pre-commit run`.

### 8. Frontend Prettier thiếu dependency trong node_modules

Vấn đề:

```text
Cannot find module 'tailwindcss-animate'
```

Nguyên nhân:

- `tailwindcss-animate` đã có trong `package.json` và lockfile.
- Nhưng `frontend/node_modules` chưa được sync.

Giải pháp:

```bash
cd frontend
corepack pnpm install --frozen-lockfile
```

## Tests Đã Thêm

Các file test đã tạo:

- `backend/tests/test_csv_reader.py`
- `backend/tests/test_excel_reader.py`
- `backend/tests/test_postgresql_reader.py`
- `backend/tests/test_reader_factory.py`
- `backend/tests/test_request_builder.py`
- `backend/tests/test_source_loader.py`
- `backend/tests/test_minio_file_resolver.py`
- `backend/tests/test_source_loader_minio_integration.py`

Fixture dữ liệu:

- `backend/tests/fixtures/admissions/2026/applicants.csv`
- `backend/tests/fixtures/admissions/2026/applicants.xlsx`

## Lệnh Test Chính

Chạy unit tests readers:

```bash
PYTHONPATH=backend .venv/bin/python -m pytest \
  backend/tests/test_csv_reader.py \
  backend/tests/test_excel_reader.py \
  backend/tests/test_postgresql_reader.py \
  backend/tests/test_reader_factory.py \
  backend/tests/test_request_builder.py \
  backend/tests/test_source_loader.py \
  -v --no-cov
```

Chạy MinIO resolver và source loader:

```bash
PYTHONPATH=backend .venv/bin/python -m pytest \
  backend/tests/test_minio_file_resolver.py \
  backend/tests/test_source_loader.py \
  -v --no-cov
```

Chạy integration test thật với MinIO:

```bash
RUN_MINIO_INTEGRATION=1 \
PYTHONPATH=backend .venv/bin/python -m pytest \
  backend/tests/test_source_loader_minio_integration.py \
  -v -s --no-cov
```

Kết quả integration đã kiểm tra:

```text
DataSource 1 CSV: rows_read=3
DataSource 2 Excel: rows_read=3
```

## Lệnh Kiểm Tra Chất Lượng

Ruff:

```bash
.venv/bin/ruff check backend/app/modules/ingestion/readers backend/tests/test_*reader.py
```

Mypy backend app:

```bash
cd backend
../.venv/bin/mypy app
```

Pre-commit:

```bash
.venv/bin/pre-commit run
```

## File Chính Đã Thay Đổi

Backend:

- `backend/app/modules/ingestion/readers/contract.py`
- `backend/app/modules/ingestion/readers/csv_reader.py`
- `backend/app/modules/ingestion/readers/excel_reader.py`
- `backend/app/modules/ingestion/readers/postgresql_reader.py`
- `backend/app/modules/ingestion/readers/factory.py`
- `backend/app/modules/ingestion/readers/request_builder.py`
- `backend/app/modules/ingestion/readers/source_loader.py`
- `backend/app/modules/ingestion/readers/minio_file_resolver.py`
- `backend/app/repositories/datasource_repo.py`
- `backend/pyproject.toml`

Tests:

- `backend/tests/test_csv_reader.py`
- `backend/tests/test_excel_reader.py`
- `backend/tests/test_postgresql_reader.py`
- `backend/tests/test_reader_factory.py`
- `backend/tests/test_request_builder.py`
- `backend/tests/test_source_loader.py`
- `backend/tests/test_minio_file_resolver.py`
- `backend/tests/test_source_loader_minio_integration.py`

## Giới Hạn Hiện Tại

- CSV/Excel remote chỉ hỗ trợ `s3://...`.
- CSV/Excel reader vẫn đọc qua file local tạm, chưa stream trực tiếp từ MinIO.
- PostgreSQL reader đọc `SELECT *`, chưa hỗ trợ query tùy chỉnh, limit, projection hoặc incremental read.
- `ReadResult.rows` đang load toàn bộ dữ liệu vào memory, phù hợp test/demo nhưng cần cân nhắc batch/streaming cho dataset lớn.
- MinIO resolver yêu cầu `source_table` khớp tên file cuối của object key.

## Hướng Phát Triển Tiếp Theo

- Hỗ trợ batch đọc để tránh load toàn bộ rows vào memory.
- Hỗ trợ projection columns hoặc limit cho preview data.
- Hỗ trợ incremental ingestion theo watermark.
- Hỗ trợ JSON reader và REST API reader.
- Ghi raw data xuống Bronze sau khi đọc.
- Tạo ingestion job tracking gồm status, rows_read, error_message, started_at, finished_at.
- Thêm observability/logging cho source loader và resolver.
