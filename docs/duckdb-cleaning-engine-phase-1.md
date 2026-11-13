# Báo cáo Giai đoạn 1 — Bộ làm sạch DuckDB

Ngày kiểm tra: **13/11/2026**. Môi trường: macOS ARM64, Python 3.13.7.

## 1. Kết quả thực hiện

Đã sửa bộ làm sạch DuckDB và nối vào `CleaningPipeline`. Kết quả được đối chiếu với ba hàm làm sạch trước đây, gồm cả dữ liệu đạt, dữ liệu cách ly, thứ tự vấn đề, vị trí dòng, phiên bản quy định và thông tin nguồn.

Đây là cách xử lý kết hợp **SQL và hàm kiểm tra Python**. Bước tìm ô thiếu và tìm nhóm trùng thực hiện bằng SQL. Bước định dạng sử dụng hàm Python hiện có, được DuckDB gọi và lưu kết quả trước khi chọn dòng lỗi. Chưa chuyển toàn bộ quy định định dạng sang SQL thuần và chưa chứng minh được tăng tốc.

Không cần migration cho thay đổi này vì cấu trúc PostgreSQL không thay đổi.

## 2. Luồng xử lý sau khi sửa

1. Đọc đúng phiên bản tệp Bronze qua bộ đọc hiện có: CSV dùng DuckDB, Excel dùng openpyxl. Thông tin nguồn, phiên bản tệp, dấu kiểm chứng và số dòng vẫn được kiểm tra ở luồng đọc Bronze.
2. `CleaningPipeline` chuyển dữ liệu và ba bộ quy định sang `DuckDBCleaningEngine`.
3. Mở một kết nối DuckDB trong bộ nhớ, dành riêng cho lần chạy.
4. Nạp theo lô bằng Arrow. Tên cột tạm do hệ thống đặt, không dùng trực tiếp tên cột của người dùng.
5. SQL tìm ô thiếu theo đúng dấu hiệu được cấu hình cho từng cột. Giá trị không phải chuỗi không bị đổi thành chuỗi để so với dấu hiệu ô trống.
6. Ô được phép trống được lưu thành `None`; dòng thiếu giá trị bắt buộc được tách riêng. Việc bỏ khoảng trắng để so dấu hiệu ô trống chỉ xảy ra khi quy định yêu cầu.
7. DuckDB gọi hàm kiểm tra định dạng hiện có theo từng cột. Hàm này giữ nguyên cách hiểu số, ngày, thời gian, nhãn thay thế, giới hạn giá trị và mã lỗi. SQL lưu kết quả và chọn các dòng không đạt.
8. Các giá trị khóa được biểu diễn bằng mã nhóm tạm, dựa trên cách so sánh của luồng cũ. SQL phân nhóm khóa, bỏ qua khóa chưa đầy đủ và tìm nhóm trùng.
9. Trả `CleaningResult`, `CleaningReport`, danh sách dòng cách ly và dữ liệu đã làm sạch. Các bước chuẩn hóa và công bố Silver phía sau tiếp tục dùng kết quả này.
10. Đóng kết nối khi thành công hoặc khi có lỗi. Nếu bên gọi cung cấp kết nối cho một bước riêng lẻ, bên gọi tiếp tục quản lý kết nối đó.

## 3. Những lỗi đã sửa

| Vấn đề trước khi sửa | Cách xử lý hiện tại |
| --- | --- |
| Ép mọi khóa thành chuỗi khiến số `7` và chuỗi `"7"` bị báo trùng | Giữ cách so sánh giá trị của bộ phát hiện trùng cũ; không ép khóa sang chuỗi |
| Khóa chỉ chứa khoảng trắng bị tính là nhóm trùng | Dùng lại quy định xác định khóa chưa đầy đủ của luồng cũ |
| Nguy cơ mất số 0 ở đầu mã | `"007"` được giữ nguyên và phân biệt với `"7"` ở bước tìm trùng; việc đối chiếu mã nội bộ vẫn thuộc bước chuẩn hóa định danh |
| Tên `_locator`, `_row_idx`, `ID`/`id` gây lỗi bảng tạm | Dùng tên cột nội bộ theo vị trí, giữ nguyên tên nguồn trong kết quả trả về |
| Dữ liệu được nạp vào DuckDB nhưng bước missing không dùng kết quả SQL | SQL trực tiếp tìm các ô thiếu, Python đóng gói kết quả theo cấu trúc cũ |
| Bước format chỉ duyệt Python sau khi nạp bảng không dùng đến | DuckDB gọi quy tắc kiểm tra qua hàm đăng ký, lưu kết quả và chọn dòng lỗi bằng SQL; giữ nguyên hàm định dạng để bảo toàn hành vi |
| Pipeline và engine phụ thuộc vòng vào nhau | Tách cấu trúc dữ liệu, báo cáo và kiểm tra đầu vào sang tệp dùng chung |
| Mô-đun xử lý nhập trực tiếp cấu hình từ hạ tầng | Lắp ghép kết nối qua `providers`, dùng giao diện kết nối độc lập |
| Thiếu khai báo kiểu `DuckDBCleaningEngine` | Nhập engine theo một chiều; mypy của ứng dụng đã đạt |
| Cấu hình DuckDB lỗi bị bỏ qua | Báo lỗi cấu hình, không âm thầm bỏ giới hạn bộ nhớ hoặc số luồng |
| Hàm tiện ích đọc Bronze chưa bắt buộc phiên bản tệp | Kiểm tra thông tin nguồn và phiên bản giống hàm đọc Bronze trước đây trước khi thực hiện đọc |

Các comment và phần giải thích được thêm/sửa trong bộ làm sạch, lớp kết nối và kiểm thử đều viết bằng tiếng Việt.

## 4. Các file chính để review

| File | Vai trò |
| --- | --- |
| `app/modules/pipeline/cleaning/duckdb_engine.py` | Xử lý dữ liệu thiếu, gọi kiểm tra định dạng, tìm trùng và tạo kết quả |
| `app/modules/pipeline/cleaning/pipeline.py` | Điều phối gọi engine, giữ cách sử dụng của các nơi đang gọi pipeline |
| `app/modules/pipeline/cleaning/contracts.py` | Cấu trúc dữ liệu đầu vào, kết quả, báo cáo và kiểm tra bằng chứng Bronze |
| `app/modules/pipeline/contracts/cleaning.py` | Giao diện yêu cầu tính toán, không nhập thư viện DuckDB |
| `app/infra/duckdb/cleaning.py` | Kết nối DuckDB, nạp Arrow theo lô, đăng ký hàm kiểm tra và đóng kết nối |
| `app/providers.py` | Lắp ghép bộ xử lý với kết nối cụ thể |
| `tests/test_duckdb_cleaning_engine.py` | 44 trường hợp kiểm tra engine, gồm đối chiếu trực tiếp với các hàm cũ |

## 5. Phạm vi kiểm thử tương đương

Kiểm thử so sánh toàn bộ đối tượng kết quả, không chỉ số dòng thành công:

- `None`, chuỗi rỗng, dấu hiệu `NULL`, dấu hiệu chưa khai báo, trường cho phép trống và trường bắt buộc.
- Khoảng trắng thông thường, xuống dòng, dấu cách không ngắt dòng và dấu cách toàn chiều rộng; có và không bật bỏ khoảng trắng.
- Phân biệt chuỗi với số; giữ cách so sánh số nguyên, số thập phân và giá trị đúng/sai của bộ tìm trùng cũ.
- Khóa chính, khóa duy nhất, khóa ghép, nhiều quy định trùng chồng lên nhau, có và không cho phép gộp dòng giống hệt.
- Định dạng chữ, số nguyên, số thập phân, ngày, thời điểm có múi giờ và giá trị đúng/sai.
- Dòng có nhiều lỗi; thứ tự lỗi và việc không ghi nhận sửa thành công cho dòng bị loại.
- Tên cột có dấu nháy, dấu gạch chéo, tên giống cột nội bộ hoặc khác nhau chỉ bởi chữ hoa/chữ thường.
- Lô không có dòng, ba phiên bản quy định khác nhau, thông tin nguồn và thứ tự dòng sau khi xử lý.
- Bắt buộc phiên bản Bronze, báo lỗi cấu hình và đóng kết nối khi một bước thất bại.
- Tệp CSV/XLSX/XLSM qua luồng làm sạch, chuẩn hóa và xuất bản Parquet trong kiểm thử cô lập hiện có.

## 6. Kết quả kiểm tra

| Kiểm tra | Kết quả |
| --- | --- |
| Toàn bộ `pytest` | **1.431 đạt, 52 bỏ qua**, độ bao phủ **87,17%** |
| Kiểm tra bổ sung sau thay đổi nạp bước format: engine, kiến trúc, luồng nguồn → Silver | **98 đạt** |
| Kiểm thử kiến trúc | Đạt; không còn vòng phụ thuộc hoặc nhập hạ tầng trực tiếp từ engine |
| `ruff check app tests` | Đạt |
| Định dạng các file thuộc phần sửa | Đạt |
| `mypy --config-file pyproject.toml app` | Đạt, 176 file nguồn |
| Pyright các file thuộc phần sửa và kiểm thử engine | Không có lỗi |
| Pyright toàn dự án | Đã sửa cả 28 lỗi; kết quả mới là **0 lỗi, 0 cảnh báo** |
| Kiểm tra định dạng toàn dự án | Đạt, **249 file** đúng định dạng; đã sửa hai file `app/modules/pipeline/models/identity.py` và `app/modules/pipeline/routers/quarantine_records.py` |

Các trường hợp bỏ qua gồm kiểm thử cần PostgreSQL/MinIO thật và trường hợp không áp dụng với cấu trúc nguồn được chọn. Không suy ra rằng kiểm thử hạ tầng thật đã đạt. Các bước kiểm tra mã nguồn đã đạt khi chạy local; đợt này không chạy lại GitHub Actions, build image hoặc triển khai dịch vụ.

Bổ sung theo yêu cầu sửa lỗi kiểm tra kiểu và định dạng:

- `test_cleaning_acceptance.py`: truyền ngày giờ có múi giờ đúng kiểu; xác nhận cột tùy chọn, trang tính và bằng chứng nguồn tồn tại trước khi sử dụng. Hàm tạo mã số khác chỉ dùng trong dữ liệu kiểm thử và kiểm tra kiểu trước khi tăng mã.
- `test_quarantine_records.py`: dùng `IdentityResult` và `IdentityIssue` thật thay cho đối tượng giả chỉ có vài thuộc tính, giữ nguyên các tình huống kiểm tra gộp lỗi và ghép cột nguồn.
- Hai file định dạng chỉ được bổ sung dòng trống và khoảng trắng sau dấu comment, không thay đổi cách xử lý dữ liệu.
- Không tắt quy định Pyright, không dùng chỉ thị bỏ qua lỗi và không loại bỏ các kiểm thử để làm kết quả kiểm tra đạt.

Lệnh kiểm tra phần thay đổi:

```bash
.venv/bin/pytest -q --no-cov \
  tests/test_duckdb_cleaning_engine.py \
  tests/test_module_boundaries.py \
  tests/test_source_cleaning.py

.venv/bin/mypy --config-file pyproject.toml app

.venv/bin/ruff check app tests
```

Lệnh kiểm tra toàn bộ:

```bash
.venv/bin/pytest -q --cov-report=term:skip-covered
.venv/bin/ruff format app tests --check
.venv/bin/pyright --pythonpath .venv/bin/python
```

## 7. Đo thử 20.000 và 30.000 dòng

Dùng dữ liệu giả lập trong bộ nhớ với ba cột: mã có số 0 ở đầu, tên cần bỏ khoảng trắng theo cấu hình, số thập phân. Có dòng thiếu tên và dòng trùng hoàn toàn. So sánh toàn bộ `CleaningResult` của engine với cách ghép ba hàm làm sạch cũ.

| Đầu vào | Đạt | Cách ly | Gộp trùng | Kết quả cũ/mới | Luồng cũ | DuckDB |
| --- | --- | --- | --- | --- | --- | --- |
| 20.000 | 19.940 | 21 | 39 | Giống nhau | 0,1874 giây | 0,8788 giây |
| 30.000 | 29.910 | 31 | 59 | Giống nhau | 0,3543 giây | 0,4270 giây |

Đây là một lượt đo mỗi kích thước, theo thứ tự 20.000 rồi 30.000, không gồm đọc MinIO, gọi API, ghi PostgreSQL hoặc xuất Parquet. Lượt DuckDB đầu tiên còn gồm chi phí khởi tạo các thành phần cần dùng. Không dùng các số này để khẳng định tăng tốc hay suy ra thời gian chạy production.

Kết quả hiện tại chứng minh tính tương đương trên các lô đã đo. Cả hai lượt chưa nhanh hơn luồng cũ; nếu mục tiêu tiếp theo là tăng tốc, cần đo nhiều lượt và tối ưu các quy định định dạng thường dùng bằng SQL, tiếp tục giữ bộ kiểm thử đối chiếu.

## 8. Giới hạn còn lại

- Bước định dạng vẫn dùng hàm Python qua DuckDB; các vòng lặp đóng gói báo cáo và giữ nguyên kiểu dữ liệu vẫn cần thiết.
- Bộ đọc hiện có và kết quả làm sạch vẫn giữ lô dữ liệu trong bộ nhớ. Đây chưa phải luồng đọc/xử lý theo từng phần cho tệp rất lớn. Giới hạn bộ nhớ DuckDB không bao gồm toàn bộ dữ liệu Python.
- Chưa đo tải đồng thời hoặc thời gian chạy với PostgreSQL/MinIO thật trong đợt này.
- Các bước mapping, chuẩn hóa kiểu/định danh và công bố Silver tiếp tục có quy định riêng. Việc làm sạch đạt không tự động có nghĩa dữ liệu đã được công bố.

Giai đoạn 1 đã có engine sử dụng DuckDB và đã kiểm chứng tính tương đương trong phạm vi nêu trên. Việc chuyển toàn bộ format sang SQL thuần và nghiệm thu hiệu năng vẫn là phần cần làm tiếp nếu yêu cầu những mục tiêu đó.
