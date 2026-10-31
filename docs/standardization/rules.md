# S1 — Catalogue quy tắc chuẩn hóa, version 0.1.0-draft

Trạng thái: thiết kế có kiểm thử contract; chưa được activate production. Tất cả rule dưới đây version 0.1.0. Lỗi error cách ly bản ghi và chặn snapshot theo D06; lỗi cấu hình chặn run trước đọc toàn bộ. Issue phải có entity/field/locator/rule/version/code, không lộ raw PII. Không coi optional field chuyển đổi lỗi là null hợp lệ.

## Quy tắc nền tảng

| Rule ID | Phạm vi / phép xử lý xác định | Ví dụ | Lỗi / hành vi |
|---|---|---|---|
| MAP-01 | Từng entity/profile: đổi tên tường minh theo mapping-matrix; không fuzzy match. Cột ngoài schema giữ ở Bronze, loại khỏi Silver và báo drift | MaSV → student_code chỉ khi profile khai báo | REQUIRED_SOURCE_FIELD_MISSING chặn batch; MAPPING_CONFLICT khi hai cột khác giá trị cùng target |
| TYPE-01 | Theo x-physical-type của mỗi field; kiểm tra các rule TYPE-02–07 trước và sau chuyển đổi | "3" → int32 3 | TYPE_CONVERSION_ERROR, không dùng giá trị mặc định |
| TYPE-02 | Chuỗi UTF-8, Unicode NFC; trim hai đầu cho code/name, giữ dấu và khoảng trắng bên trong; không uppercase hàng loạt. Giữ số 0 đầu | " 0012 " → "0012" | STRING_LENGTH_ERROR, PATTERN_ERROR; không cắt ngắn |
| TYPE-03 | int32/int64: chỉ cú pháp nguyên và giới hạn có dấu; không nhận bool, phần lẻ hoặc scientific notation nếu profile chưa khai báo | "12" → 12; "12.7" lỗi | INTEGER_OVERFLOW / INTEGER_PARSE_ERROR |
| TYPE-04 | Decimal chính xác, không đi qua float; source separator phải được khai báo; target scale cố định, thêm zero nếu thiếu scale, dư scale thì reject trong v0.1 | "25,5" ở profile dấu phẩy → Decimal 25.50 | DECIMAL_PARSE_ERROR / DECIMAL_SCALE_ERROR; "1,234" không đoán locale |
| TYPE-05 | Date chỉ nhận format nguồn khai báo; target date32/ISO date, không dịch timezone. Native date được kiểm tra trực tiếp | "31/12/2026" với dd/MM/yyyy → 2026-12-31 | DATE_PARSE_ERROR; 31/02 bị từ chối |
| TYPE-06 | Timestamp target microsecond UTC. Source naive phải có timezone trong profile; không suy từ timezone máy. Offset-aware đổi UTC; precision vượt microsecond reject | 2026-01-01T07:00:00+07:00 → 2026-01-01T00:00:00Z | TIMEZONE_REQUIRED / TIMESTAMP_PARSE_ERROR |
| TYPE-07 | Boolean native hoặc dictionary per source; không truthiness chuỗi | "false" → false; "Có" → true chỉ khi profile định nghĩa | INVALID_BOOLEAN |
| NULL-01 | Tất cả field target phải hiện diện, nullable cho phép JSON null; missing khác null. Null tokens theo field/source; mặc định baseline CSV chỉ chuỗi rỗng, không tự coi 0 hoặc "NA" là null | nullable applicant_id = null hợp lệ | REQUIRED_VALUE_MISSING hoặc REQUIRED_SOURCE_FIELD_MISSING |
| VAL-01 | Enum target giữ đúng case trong schema; baseline nguồn chỉ chấp nhận đúng enum. Alias nguồn ngoài baseline phải có dictionary được duyệt và pin version | Active → Active; "Đang học" chưa có dictionary thì reject | UNKNOWN_REFERENCE_CODE |
| ID-01 | Source PK trong namespace hệ thống + entity; tìm/cấp canonical BIGINT bền vững. Không copy source PK trực tiếp hoặc nối người qua tên | nguồn A ID 7 và nguồn B ID 7 chưa chắc cùng người | AMBIGUOUS_IDENTITY / SOURCE_ID_MISSING |
| ID-02 | Resolve FK theo namespace được binding khai báo và crosswalk của entity đích; nullable null giữ null; giá trị có mặt nhưng chưa resolve không đổi null | class_lecturer.lecturer_id → lecturer.employee_id | REFERENCE_PENDING / AMBIGUOUS_IDENTITY |
| ID-03 | Lecturer dùng canonical employee_id. Employee_qualification giữ cặp canonical employee_id/qualification_id. Source composite key phải serialize tuple có cấu trúc | [104,103], không ghép chuỗi thiếu phân cách | DUPLICATE_KEY / REFERENCE_PENDING |
| KEY-01 | PK bắt buộc và không trùng; unique/unique_together chỉ kiểm tra tuple không null, độc lập nullable | hai student có applicant_id null hợp lệ | DUPLICATE_KEY; không dedupe bằng lấy dòng đầu |
| PII-01 | Loại direct PII trong pii_columns và source text khỏi analytics. Identity matching và luật cần PII chạy trước projection trong vùng hạn chế | full_name không có trong Silver | PII_LEAK chặn publish; raw value không vào issue thường |
| PII-02 | student/employee birth_year = năm date_of_birth hợp lệ; nguồn null → null. Chưa duyệt D04 thì không activate | 2008-03-05 → 2008 | DATE_PARSE_ERROR; không tự đổi DOB lỗi thành null |
| AUDIT-01 | Metadata từ handoff/run có kiểm soát; source record ID là locator opaque, không PII. Giữ created_at nguồn riêng; processed_at không thay created_at | schema_version = 0.1.0-draft | LINEAGE_REQUIRED / VERSION_MISMATCH |
| PUB-01 | Validate physical schema, PK/FK với snapshot cha pinned; staged object chỉ visible qua published manifest | một FK lỗi → giữ snapshot cũ | SNAPSHOT_REJECTED |

## Profile nguồn baseline và trường hợp còn thiếu

Mapping matrix bao phủ source schema chính thức: 149 field nguồn, mọi field target và audit. Nó không tự tuyên bố hỗ trợ alias cấu trúc chưa được cung cấp. Profile nguồn khác phải thêm mapping version riêng ở S2.

Baseline CSV dùng UTF-8, delimiter ',', header=true, null token '', date yyyy-MM-dd, timestamp yyyy-MM-dd HH:mm:ss, decimal '.', boolean true/false theo các JSON nguồn. **Timezone timestamp baseline chưa được nguồn xác nhận**: không mặc định Asia/Saigon; activation bị chặn cho tới khi có timezone hoặc dữ liệu có offset. Giá trị null trong JSON/native dùng None. Trường nullable target vẫn required để phân biệt thiếu cấu trúc và null.

Excel có native number/date, code hiển thị 0007 nhưng lưu 7 không đủ căn cứ khôi phục số 0. Formula không có cached result phải báo SOURCE_VALUE_UNAVAILABLE khi field cần giá trị, không coi null hợp lệ. P0 contract yêu cầu giữ workbook epoch/physical locator ở ingestion. S3 viết converter; S1 chưa đọc/biến đổi workbook.

## Kiểu vật lý và đơn vị

JSON fixtures biểu diễn Decimal bằng string, Parquet phải là decimal128(p,s), không string/float. bigint → int64, int → int32, varchar/text → utf8 với kiểm tra chiều dài, boolean → bool; date32 và timestamp[us,UTC] được khai báo trong schema. x-* là constraint bổ sung; JSON Schema đơn thuần không kiểm tra FK/unique/Decimal bounds hoặc precision vật lý.

- admission_score theo admission_method.max_score; không quy về thang 10.
- midterm_score/final_score/total_score theo thang 10; grade_value theo thang 4. Không tự suy formula trọng số hoặc bảng quy đổi letter_grade.
- tuition_fee_per_credit: VND/tín chỉ; duration_years: năm; credit_hours: số tín chỉ; semester_no: số thứ tự học kỳ; preference_rank: thứ tự nguyện vọng; year_obtained/birth_year: năm lịch.
- Trường code giữ string và pattern nguồn; canonical PK/FK là BIGINT, không chuyển code thành số.

## Luật nghiệp vụ nguồn và dẫn xuất

`business-rule-review.json` giữ đầy đủ **39 luật nguồn** và độ nghiêm trọng gốc. Chúng chưa được implement như engine. Những luật cần PII (tuổi, tên/ngày sinh khớp nhau) phải đánh giá trước projection; không đưa PII trở lại Silver để làm test.

Câu "PK và cột unique không null" trong luật nguồn mâu thuẫn nullable của national_id/applicant_id/admission_id: hợp đồng v0.1 áp KEY-01 và nullable field, cần domain owner xác nhận cách diễn đạt. Không nâng mọi unique thành NOT NULL.

Các dẫn xuất khác trong `silver_derived_columns` (GPA/attempt, current position, seniority, faculty...) **deferred**, vì cần lịch hiệu lực và quyết định nghiệp vụ. S1 v0.1 giữ dữ liệu chuẩn hóa gốc và birth_year tối thiểu; không âm thầm triển khai derived KPI. Mỗi luật nguồn phải được phân loại chuẩn hóa bắt buộc hay downstream trước G1 approval. File review ghi rõ chưa implement, không dùng PASS contract để đóng luật đó.

## Version và activation

Schema/profile/rule/dictionary/crosswalk version phải pin trong run manifest. Version draft chưa được activate. Phá kiểu, đổi meaning, bỏ field hoặc đổi khóa cần version mới và compatibility review, replay từ Bronze; không ghi đè snapshot cũ. S2–S4 thực thi rules, P5 kiểm tra writer thật, P6 chạy end-to-end.
