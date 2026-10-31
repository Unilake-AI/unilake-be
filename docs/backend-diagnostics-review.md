# Rà soát lỗi backend — 15/10/2026

## Kết luận

Đã sửa các lỗi phân tích tĩnh tái hiện được trong workspace mà không đổi quy tắc
nghiệp vụ hoặc dữ liệu mẫu. Một phần lỗi nằm trong Bronze/Ingestion mới bổ sung:
lượt kiểm tra trước chỉ xác nhận runtime/test, chưa chạy đủ mypy/Pyright.

| Kiểm tra | Trước sửa | Sau sửa |
|---|---:|---:|
| Pyright, toàn workspace | 71 lỗi / 159 file | 0 lỗi, 0 cảnh báo / 159 file |
| mypy strict, backend/app | 23 lỗi | Không có lỗi / 114 file |
| Ruff lint, backend | 3 lỗi | Đạt |
| Ruff format, backend | 4 file chưa đúng format | 150 file đạt |
| Toàn bộ pytest, bật test live Bronze/Ingestion | — | 673 passed, 81 failed, 2 skipped |
| Coverage ứng dụng | — | 91,98%, vượt ngưỡng 70% |

Pyright là kiểm tra tương ứng với nhóm diagnostics của Pylance; báo cáo này không
khẳng định đã đọc trực tiếp bảng Problems trong VS Code.

## Những gì đã sửa và lý do

| File/nhóm file | Sửa gì | Vì sao, ảnh hưởng |
|---|---|---|
| `pyrightconfig.json` | Chỉ rõ `.venv` và Python 3.11 | Giúp phân tích import/typing dùng đúng môi trường dự án. Giữ nguyên các đường dẫn module hiện có. |
| `.vscode/settings.json` (cấu hình local, đang gitignore) | Thêm interpreter mặc định `.venv/Scripts/python.exe` | VS Code trên máy Windows này tìm đúng virtualenv. Interpreter đã chọn trước đó có thể cần chọn lại thủ công. |
| `backend/pyproject.toml` | Đồng bộ mypy từ Python 3.13 về 3.11; thêm Pyright vào dev; đưa openpyxl từ extra sang dependency chính | Docker, CI và virtualenv đều dùng 3.11. Reader factory import ExcelReader trực tiếp nên openpyxl phải có ngay khi cài backend cơ bản. Không đổi thư viện xử lý Excel. |
| `.github/workflows/ci-backend.yml` | Thêm bước Pyright dùng interpreter của CI | Ngăn tái xuất hiện lỗi editor sau khi code chỉ vượt qua runtime tests. Không tắt các bước kiểm tra cũ. |
| `backend/app/infra/minio/bronze.py` | Import `ENABLED` từ `minio.commonconfig`; khai báo dictionary có giá trị `None` | Dùng đúng module công khai của SDK; version ID có thể không tồn tại. Không đổi key hoặc chế độ versioning. |
| `backend/app/modules/ingestion/contracts.py` | Khai báo `CapturedArtifact`, `BronzeWriteContext` có kiểu rõ ràng | Storage nhận thông tin job đã xác định thay vì dựa vào ORM object không rõ kiểu. Giữ hợp đồng BronzeBatch hiện có. |
| `backend/app/bronze_capture.py` | Khai báo kiểu context manager, request, artifact và output; kiểm tra location/source_table trước khi dùng | Loại lỗi dùng `None` như đường dẫn. Vẫn giữ nguyên byte CSV/Excel và export PostgreSQL thành Parquet. PyArrow chưa cung cấp typing marker trong bản đang cài: dùng `ignore[import-untyped]` riêng tại 2 import, cùng cách Silver adapter đang dùng. |
| `backend/app/modules/ingestion/execution.py` | Typed reader/store port, generic helper cho I/O; xác nhận metadata bắt buộc; tạo write context | Giữ nguyên queue, thứ tự đọc/ghi, quyền, trạng thái và lease. Metadata thiếu bị ghi FAILED qua cơ chế hiện có. Reader protocol đặt ở execution để không tạo vòng import giữa các contract. |
| `backend/app/repositories/ingestion_repo.py` | Thêm alias cho kiểu context; dùng `UPDATE ... RETURNING ingestion_job_id` thay cho truy cập `Result.rowcount` | SQLAlchemy async trả kiểu Result không bảo đảm thuộc tính rowcount. RETURNING kiểm tra đúng job vừa cập nhật; giữ nguyên điều kiện RUNNING và lease. |
| `backend/app/modules/ingestion/router.py` | Bổ sung kiểu kết quả các endpoint | Không đổi route, HTTP status hay response model. |
| `backend/app/domains/base.py`, `backend/app/modules/nlq/contracts.py` | Thêm `...` vào thân phương thức Protocol | Các phương thức là khai báo interface, không phải hàm thực thi trả về None. Không thay implementation của domain/NLQ. |
| Các test provenance, Excel, identity, ingestion | Assert đối tượng không None trước khi dùng; fixture dùng ORM object đúng kiểu | Giữ các kiểm tra cũ và xác nhận rõ điều kiện tiền đề. Không làm nullable thành non-null trong model nghiệp vụ. |
| Test bất biến/sai kiểu | Chỉ đánh dấu `pyright: ignore[...]` đúng dòng cố tình truyền sai kiểu hoặc sửa đối tượng bất biến | Đây là nội dung test cần giữ; không sửa đầu vào sai thành đúng chỉ để hết gạch đỏ. Có chú thích lý do ở từng dòng. |
| `scripts/standardization/live_demo.py`, `preview_s4.py` | Lấy `stdout.reconfigure` qua getattr và kiểm tra callable | Hỗ trợ cả stdout thường và môi trường redirect, giữ chế độ UTF-8 khi có hỗ trợ. |
| `data/synthetic/generate_admissions.py` | Tách nhánh method và assert score không None trước so sánh | Giữ nguyên công thức, cutoff và thứ tự gọi random. Không chạy lại generator và không tái sinh dữ liệu. |
| 4 file chưa đúng format | Ruff format; tách assert kép và sửa cú pháp parametrize trong test Silver | Thay trình bày/khai báo test, không thay expected values hoặc quy tắc nghiệp vụ. |

Các test chỉnh trong đợt rà soát: `test_excel_reader.py`, `test_file_handoff.py`,
`test_identity_standardization.py`, `test_ingestion_execution.py`,
`test_ingestion_execution_live.py`, `test_live_demo_files.py`,
`test_reader_standardization_integration.py`, `test_schema_mapping.py`,
`test_silver_contracts.py`, `test_silver_publication.py`,
`test_type_standardization.py`. Hai file chỉ format thêm:
`test_dataset_schemas.py`, `test_source_loader_minio_integration.py`.
`app/modules/ingestion/readers/factory.py` chỉ gộp chuỗi theo formatter.

Các thay đổi storage, Docker, schema, migration và API có từ hai tác vụ trước
vẫn được giữ. Đợt rà soát này không sửa thêm cấu trúc DB hoặc reset volume.

## Các lỗi test đã có trong bản gốc

Đã trích riêng Git HEAD, chạy toàn bộ test bằng cùng Python 3.11 và so sánh tên
từng test thất bại với checkout hiện tại:

- HEAD: 81 failed, 632 passed, 2 skipped.
- Hiện tại, bật thêm hai nhóm live test: 81 failed, 673 passed, 2 skipped.
- Tập tên 81 test thất bại giống nhau hoàn toàn; không có test thất bại mới.
- Có 78 lỗi liên quan dataset/schema/CSV, 3 lỗi kiểm tra ranh giới module.

Ví dụ cụ thể:

- Dataset admissions test còn yêu cầu entity `enrollment`, nhưng schema không có entity này.
- Nhiều CSV mẫu không khớp header/schema; một số file training như `enrollment.csv` còn thiếu.
- HR test còn trông đợi `employee_id` ở dữ liệu không có cột đó.
- Ranh giới module cũ: `core.deps` import DB session; request_builder import datasource model;
  một số module khác phụ thuộc trực tiếp infrastructure.

Các lỗi này độc lập với gạch đỏ typing. Chưa sửa bằng cách đổi CSV/schema, đổi
luật nghiệp vụ, bỏ test hoặc nới kiểm tra kiến trúc. Cần xác định bộ schema/dataset
nào là nguồn chuẩn trước khi đồng bộ nhóm test dữ liệu.

Log kiểm chứng được giữ cục bộ dưới `logs/backend-audit-20261015/` (gitignore).
Test live sử dụng schema PostgreSQL/bucket MinIO riêng và tự dọn sau khi chạy.
Hai test bỏ qua là integration MinIO catalog cũ cần `RUN_MINIO_INTEGRATION=1`;
không tự chạy chúng với ID nguồn cố định trong catalog của người dùng.

## Nếu VS Code vẫn hiển thị diagnostics cũ

Chạy `Python: Select Interpreter`, chọn `D:\Caps1\UniDataLake_AI\.venv\Scripts\python.exe`,
sau đó `Python: Restart Language Server` hoặc `Developer: Reload Window`.
Cấu hình mặc định không tự thay interpreter đã được VS Code lưu cho workspace.

Lệnh kiểm tra lại từ thư mục gốc:

```powershell
.\.venv\Scripts\python.exe -m pyright
.\.venv\Scripts\python.exe -m mypy --config-file backend/pyproject.toml backend/app
.\.venv\Scripts\python.exe -m ruff check backend
.\.venv\Scripts\python.exe -m ruff format backend --check
```
