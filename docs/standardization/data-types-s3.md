# S3 — Chuẩn hóa kiểu dữ liệu

Implementation: `backend/app/modules/pipeline/type_standardization.py`. Bộ chuyển đổi thuần Python, không I/O, không SQL tùy ý, không ghi Silver. Dùng native Decimal/date/datetime/bool; không dùng chuỗi JSON để giả vờ đã có kiểu vật lý Parquet.

## Luồng xử lý

MappingResult S2 → kiểm tra MappingKey/target schema version → đọc field spec S1 → chuyển đổi với FieldOptions per field → kiểm tra range/length/pattern → TypedRow hoặc issue/rejected locator.

TypeStandardizationService nhận key, conversion_version, contract S1 và options. Không tự lấy profile mới nhất. Result giữ key, input_version, batch_id, conversion_version, configuration_fingerprint, mapping_issues, preview/counts và lỗi S3. Không chứa lại toàn bộ raw input hoặc preview before của S2. `conversion_version` do caller quản lý; kho profile và kiểm soát kích hoạt thuộc tích hợp P5, chưa có persistent registry S3.

## Cấu hình bất biến và kiểm tra sớm (15/10/2026)

Service là frozen dataclass. Schema được chụp riêng qua JSON, khóa đệ quy bằng mapping proxy và tuple; options được sao chép vào mapping chỉ đọc, mỗi FieldOptions là frozen với token tuple. Không thể sửa version/key/spec/options qua phép gán thông thường; thay dictionary/list contract ban đầu không đổi service đã tạo. Muốn đổi rule phải tạo service/cấu hình mới. Đây là bảo đảm chống sửa vô tình qua API Python, không phải sandbox chống code cố ý gọi cơ chế thấp cấp như object.__setattr__.

Constructor kiểm tra dialect contract S1 của entity được chọn: field required/PK/unique, type vật lý–logic, Decimal precision/scale, length/range/pattern, enum, cấu trúc reference và derived birth_year. Contract thiếu physical type, bound NaN/Infinity, regex sai hoặc kiểu không hỗ trợ trả ConversionConfigurationError trước xử lý batch, kể cả batch rỗng. FK chỉ được kiểm tra khai báo; sự tồn tại của record đích và identity resolution vẫn thuộc S4/P5. Đây không phải validator cho mọi tính năng JSON Schema tùy ý.

`configuration_fingerprint` là SHA-256 của key, version, snapshot contract, options hiệu lực (kể cả defaults) và nhãn dialect engine. Thứ tự key trong JSON/dictionary không đổi fingerprint; thay rule/schema/version làm fingerprint đổi. Fingerprint được trả trên TypeResult và hiển thị trong CLI preview. P5 cần lưu cùng run và so sánh khi replay/đăng ký profile; S3 chưa có kho toàn cục để cấm caller tạo hai service khác nội dung nhưng cùng conversion_version. Fingerprint cũng không thay cho việc pin phiên bản phần mềm và timezone database của môi trường.

## Chuyển đổi đã có

Cập nhật 15/10/2026: format tùy chỉnh được kiểm tra trước xử lý dữ liệu. Date bắt buộc `%Y`, `%m`, `%d`; timestamp bắt buộc thêm `%H`, `%M`, `%S`, có thể dùng `%f` và `%z`. Mỗi directive chỉ xuất hiện một lần; `%%` là ký tự phần trăm, không tính là thành phần ngày giờ. Không nhận năm hai chữ số, format theo locale (`%x`/`%c`), tên timezone `%Z`, week-date hoặc giờ 12 tiếng. Cần đổi profile cũ dùng các format này sang format số tường minh.

Format thiếu thành phần trả `ConversionConfigurationError` với `INVALID_DATE_FORMAT` hoặc `INVALID_TIMESTAMP_FORMAT`, kể cả dữ liệu null; không bị policy optional-to-null che mất. Input thiếu thành phần so với format hợp lệ trả lỗi parse theo bản ghi. Tháng/ngày/giờ/phút/giây phải đủ hai chữ số; `%Y` đủ bốn chữ số. Timestamp chỉ có ngày không được tự điền 00:00:00, date thiếu năm không được tự điền 1900. Giá trị 00:00:00 ghi rõ trong nguồn vẫn hợp lệ.

| Kiểu | Quy tắc |
|---|---|
| String | Chỉ nhận string; Unicode NFC; trim khi cấu hình; giữ 0 đầu, không tự uppercase; kiểm tra maxLength/pattern |
| Integer | String số nguyên hoặc native int; reject bool, số lẻ, float; giới hạn signed int32/int64 và min/max nghiệp vụ |
| Decimal | Decimal/native int/string theo locale; không nhận float, NaN/Infinity hoặc scientific string; validate nhóm nghìn, precision/scale, giới hạn. Thêm zero cho scale thiếu; dư scale reject, không rounding ngầm |
| Date | Native date hoặc string theo format duy nhất; reject datetime để không âm thầm bỏ phần giờ; ngày sai hoặc không đúng format bị lỗi |
| Timestamp | ISO hoặc format nguồn khai báo; native datetime được hỗ trợ; quy UTC. Naive thiếu timezone bị lỗi. DST ambiguous/nonexistent bị lỗi, không tự chọn fold |
| Boolean | Native bool hoặc bộ true/false tokens khai báo; không coi mọi chuỗi khác rỗng là true hoặc unknown là false |
| Null | None hoặc null tokens theo field; nullable mới chấp nhận; missing field vẫn lỗi cấu trúc. 0 không mặc nhiên là null |
| Excel serial date | Chỉ xử lý khi khai báo epoch 1900/1904. Reject serial lẻ vì mất phần giờ, số âm và ngày giả 60 của hệ 1900. Không suy epoch từ giá trị |

Mặc định date `%Y-%m-%d`, timestamp `iso`, decimal `.`, không thousands separator, null_tokens rỗng, không trim. Cấu hình nguồn baseline CSV phải được caller khai báo rõ, đặc biệt null token rỗng và timestamp format/timezone. Không coi giờ máy chạy là timezone nguồn. `tzdata` đã được thêm dependency để hỗ trợ IANA timezone trên Windows/container.

Decimal dùng context cục bộ để hỗ trợ precision đến 38 của S1 contract. Excel số thập phân do reader trả float bị reject; cần quyết định cách biểu diễn chính xác từ reader/source profile trước khi cho phép, không tự `Decimal(float)` hoặc làm tròn mất dữ liệu.

## Ranh giới với S4 và P5

TypedRow gồm:

- `values`: field đã chuyển kiểu và kiểm tra quy tắc kiểu S1.
- `pending_s4`: PK/FK nguồn và các nhãn enum. ID giữ nguyên string/int, reject bool/object; không cast `007` thành 7. Enum được kiểm tra kiểu chuỗi, chuẩn NFC/trim nếu cấu hình, nhưng chưa đối chiếu dictionary.

Không cho chính sách invalid_optional_to_null áp dụng PK/FK. Optional enum có lỗi kiểu chỉ đổi null nếu profile cho phép, có warning và vẫn nằm trong pending_s4. Unknown label hợp lệ kiểu vẫn chờ S4, không được coi là enum target hợp lệ.

Audit `_...` để P5 bổ sung từ run, không sao chép metadata giả từ nguồn. Thiếu created_at nghiệp vụ bị lỗi; không thay bằng processed_at. PK/FK/enum chưa resolved, audit chưa đủ và 39 luật nghiệp vụ chưa thực thi nên **silver_ready luôn false**.

## Derived birth_year và PII

student/employee birth_year lấy từ restricted `derivation_inputs[locator]['date_of_birth']`. Caller phải cung cấp `derivation_input_version` bằng input_version của MappingResult. Thiếu input không được dùng birth_year nguồn tự khai hoặc default năm hiện tại. DOB null hợp lệ → birth_year null; DOB sai → lỗi. DOB không được đưa vào TypedRow hoặc issue. Đây chỉ kiểm tra version được caller cung cấp, không tự xác minh checksum/storage; ingestion phải cung cấp dữ liệu đáng tin cậy.

Quyền đọc Bronze/DOB, approval D04 và retention không được engine tự cấp. P5/API phải kiểm tra quyền trước khi gọi service. S3 hiện chưa đọc trực tiếp Bronze.

## Chính sách lỗi

- Field bắt buộc hoặc field tùy chọn lỗi mặc định: reject cả record, không trả partial typed record là accepted.
- Optional field có `invalid_optional_to_null=True`: đổi null kèm warning, chỉ khi target nullable và không phải identity. Missing structure/derivation input không được che bằng policy này.
- TypeIssue: locator, field, code, severity, rule_version; batch/key/input version ở TypeResult. Không có raw value.
- Lỗi S2 giữ trong mapping_issues và rejected_locators; S3 không phục hồi âm thầm row đã bị S2 loại.
- Preview vẫn chỉ là preview, giữ inspected_count/total_count. Accepted là qua bước S3, không phải cho phép publish.

Mã lỗi chính: INTEGER_PARSE_ERROR, INTEGER_OVERFLOW, DECIMAL_PARSE_ERROR, DECIMAL_SCALE_ERROR, DECIMAL_OVERFLOW, DATE_PARSE_ERROR, DATE_TIME_LOSS, EXCEL_EPOCH_REQUIRED, EXCEL_FICTITIOUS_DATE, TIMESTAMP_PARSE_ERROR, TIMEZONE_REQUIRED, AMBIGUOUS_LOCAL_TIME, NONEXISTENT_LOCAL_TIME, INVALID_BOOLEAN, STRING_TYPE_ERROR, STRING_LENGTH_ERROR, PATTERN_ERROR, VALUE_RANGE_ERROR, REQUIRED_VALUE_MISSING, REQUIRED_SOURCE_FIELD_MISSING, DERIVATION_INPUT_REQUIRED.

## Chạy demo S2 → S3

Tại repo root:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/standardization/preview_s3.py --case admissions-flat
.\.venv\Scripts\python.exe -X utf8 scripts/standardization/preview_s3.py --case academic-nested
.\.venv\Scripts\python.exe -X utf8 scripts/standardization/preview_s3.py --case hr-flat
```

Sáu case sẵn có dùng dữ liệu giả lập S2. JSON CLI gắn type/value cho Decimal và date để hiển thị; service thực sự trả native types. Admissions max_score `30,00` → Decimal `30.00`, is_active `Có` → bool true; Academic start_date `01/09/2026` → date `2026-09-01`; HR position_level `Nhân viên` nằm trong pending_s4.

## Kiểm chứng

55 tests S3: valid/error cases, locale, scale/overflow, 22 entity fixtures, null policy, birth_year, DST, Excel, version mismatch, kế thừa lỗi S2 và 6 CLI previews. Cùng S1/S2/domain: **237 passed**. Ruff + mypy module S3 đạt. Không chạy storage/Parquet round-trip, không chứng minh toàn bộ business rules hoặc toàn repo xanh.

Sau sửa format ngày/giờ ngày 15/10/2026: thêm 31 regression cases; tổng nhóm S1–S3/domain **268 passed**. Ruff và mypy module S3 đạt. Kết quả 237 phía trên là mốc triển khai ban đầu.

Sau khóa cấu hình và kiểm tra contract sớm: thêm 27 regression cases, tổng nhóm **295 passed**. Đã kiểm tra sửa schema lồng/tuple/options/version bị chặn, thay input gốc không ảnh hưởng, fingerprint ổn định và đổi theo nội dung, contract/options/key không hợp lệ bị từ chối ngay. Ruff và mypy module đạt.

## Cập nhật handoff S4 (15/10/2026)

Enum và các field chuỗi khai báo qua `value_mapping_fields` được giữ trong pending_s4.
S3 kiểm tra kiểu/null nhưng hoãn pattern/length đích của chúng đến sau dictionary
mapping ở S4; alias nguồn không bắt buộc có cùng độ dài với code đích. Danh sách
value_mapping_fields được pin trong configuration_fingerprint. Xem identity-values-s4.md.
