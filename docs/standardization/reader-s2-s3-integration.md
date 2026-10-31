# Kiểm thử reader → S2 → S3

Cập nhật handoff 15/10/2026: fixture nay đi qua `read_file_snapshot`, SHA256 của
bản sao thực sự được parse và FileProvenance; adapter không còn nhận ReadResult
raw chỉ kèm version tự khai báo. Provenance giữ qua S2/S3. MinIO version có kiểm
thử mock (chưa live). Entry point catalog mới là `read_data_source_batch`.
Xem [G1 resolution](g1-resolution.md); các giới hạn Excel native bên dưới vẫn còn.

## Phạm vi và cách chạy

Kiểm thử dùng file CSV/XLSX thật trong thư mục tạm, chạy reader thật, adapter
`reader_to_batch`, SchemaMappingService và TypeStandardizationService với schema S1
hiện có. Không mock parser hoặc kết quả S2/S3. Ba domain dùng admission_method,
semester, position; bổ sung department/assignment cho timestamp và nullable.

Từ thư mục `backend`, dùng Python của `.venv` và cài extra `ingest-docs` để có
openpyxl. Lệnh kiểm thử:

```powershell
..\.venv\Scripts\python.exe -m pytest tests/test_reader_standardization_integration.py tests/test_excel_reader.py tests/test_csv_reader.py tests/test_type_standardization.py tests/test_schema_mapping.py tests/test_silver_contracts.py tests/test_domains.py -o addopts='' -q
```

## Hành vi được bảo vệ

- Mapping tên cột khác schema, gồm ký tự `/` cần escape JSON Pointer.
- Decimal dạng text, ngày ISO, boolean native và cấu hình tiếng Việt.
- Giữ `0007` khi mã nguồn là text; ID nằm trong pending_s4, chưa phải ID canonical.
- Giữ sheet/dòng vật lý Excel sau metadata/header/dòng trống; CSV dùng ordinal record,
  không tuyên bố là số dòng vật lý (CSV có thể chứa xuống dòng trong một ô).
- Input version xuyên suốt S2/S3. Fixture lấy SHA256 file; adapter yêu cầu caller
  truyền version của snapshot thực tế, không tự đọc lại đường dẫn có thể đã thay đổi.
- Ô trống nullable giữ null, required bị từ chối với locator/field/code.
- Timestamp native chỉ đổi UTC khi cấu hình timezone rõ ràng.
- Serial ngày dạng số dùng epoch cấu hình tường minh, thử workbook 1900 và 1904.
- Công thức (kể cả có cache), ô lỗi Excel và dữ liệu ở cột không có header làm reader
  thất bại cả batch. Không tính công thức, không coi cache là đáng tin cậy, không
  biến công thức thành null hoặc bỏ dòng chỉ chứa công thức. Lỗi không chứa giá trị gốc.
- Adapter từ chối cột trùng, cột rỗng, độ rộng hàng sai để tránh mất giá trị khi zip.

## Giới hạn còn lại trước khi nhận Excel nghiệp vụ rộng rãi

ExcelReader vẫn giữ kiểu native của openpyxl. Ngày Excel có format ngày thường trở
thành datetime, S3 date32 từ chối DATE_TIME_LOSS; số thập phân Excel thành float,
S3 decimal từ chối DECIMAL_PARSE_ERROR. Đây là kiểm thử từ chối an toàn, **chưa phải
hỗ trợ thành công toàn bộ Excel native**. Tạm dùng text ISO/text decimal rõ định dạng.
Mã lưu dạng số không được khôi phục các số 0 đầu từ display format; nguồn phải lưu
mã dạng text. Adapter không lấy epoch hay number_format tự động để suy đoán quy tắc.

Cần bước tiếp theo lưu provenance của ô (kiểu gốc, numeric lexical value, number
format, epoch) và quy tắc được duyệt trước khi hỗ trợ chuyển đổi native rộng hơn.
Không nới lỏng S3 bằng cách bỏ giờ hoặc Decimal(float) mặc định.

Adapter chỉ là hand-off trong bộ nhớ, chưa nối vào job/API production, chưa persist
Bronze, chưa quarantine reader errors, chưa kiểm thử PostgreSQL/MinIO thật hoặc ghi
Silver. Mọi kết quả vẫn silver_ready=false; S4/crosswalk và publish gate còn cần thiết.
