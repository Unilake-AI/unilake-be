# Nhập file liên quan nhiều bảng — triển khai backend

Đối chiếu phần Giai đoạn 1 trong [đặc tả nghiệp vụ](https://docs.google.com/document/d/1rIOm43Twpna1N2HQKMFeeLPhGFC8IoG65_d-lVrAf8E/edit?tab=t.mcsb7dd1puik).
Chỉ thay đổi BE. Frontend hiện có vẫn gửi được file qua API upload cũ.

## 1. Quy tắc đã xác nhận

- Ô trống giữ giá trị cũ khi cập nhật. Khi tạo mới, trường bắt buộc vẫn phải có giá trị
  hoặc có default thực sự trong schema. Không tự tạo tên, mã hoặc trạng thái còn thiếu.
- Nhiều dòng cùng khóa có giá trị khác nhau thì báo xung đột. Giá trị trống không
  ghi đè giá trị đã có. Dòng lặp không tạo thêm thực thể hoặc liên kết.
- Là giảng viên = Có tạo/cập nhật lecturer liên kết đúng employee_id. Có → Không
  giữ hồ sơ lecturer và các liên kết hiện có.
- Một tổ hợp thí sinh + chương trình + năm + phương thức có tối đa một đăng ký.
- Thiếu mã tham chiếu: báo để bổ sung danh mục rồi nạp lại. Không tự tạo danh mục.
- Một lượt đồng bộ nghiệp vụ là một transaction. Lỗi ở bất kỳ bảng nào sẽ rollback
  dữ liệu nghiệp vụ, định danh và audit của cả file.

## 2. Nghiệp vụ được hỗ trợ

| import_type | Bảng được ghi | Bảng chỉ tra cứu | Khóa chống trùng |
| --- | --- | --- | --- |
| hr_employee | employee, lecturer | employee hiện có | employee_code; lecturer.employee_id |
| hr_assignment | assignment | employee, department, position | nguồn + assignment_code ổn định |
| hr_qualification | employee_qualification | employee, qualification | employee_id + qualification_id |
| admissions_registration | applicant, admission | program, admission_year, admission_method | nguồn + applicant_code; đăng ký theo tổ hợp 4 FK |
| academic_curriculum | curriculum, curriculum_course | program, course | program_id + version; curriculum_id + course_id |
| admissions_results | admission: điểm/kết quả | Đăng ký và phương thức đã có | Tổ hợp 4 FK + expected_revision |
| admissions_enrollment | admission: trạng thái/ngày nhập học | Đăng ký ACCEPTED đã có | Tổ hợp 4 FK + expected_revision |

Department chỉ nhận mã hệ thống PDT/PNS/PTS đang ACTIVE. Không ghi cây khoa/bộ môn
vào bảng phòng phân quyền. Qualification tra theo tên và loại nếu có; nhiều kết quả
trùng tên sẽ báo mơ hồ. Schema hiện chỉ có một hồ sơ bằng cấp cho mỗi cặp nhân viên–
bằng cấp. Thay cơ sở/năm cấp của một cặp đã có bị chặn để chốt nghiệp vụ nhiều chứng
chỉ cùng loại, tránh ghi đè mất chứng chỉ cũ.

`admissions_registration` tạo đăng ký ở trạng thái APPLIED từ giá trị file cung cấp.
File này không đổi trạng thái xét tuyển của đăng ký hiện có. Hai luồng kết quả/nhập
học đã được chấp thuận và triển khai; xem [hướng dẫn API và vận hành](admissions-workflows.md).
Khi file đăng ký sửa admission đã có, cần expected_revision hiện tại. Bộ ghi admission
generic cũ được chặn để toàn bộ thay đổi đi qua quy định nghiệp vụ và revision.

## 3. Ánh xạ đến trường DB

Danh mục đầy đủ tên cột chấp nhận, kiểu dữ liệu và từ điển giá trị được trả từ
`GET /api/v1/ingestion/import-types`. Các trường nhập dùng tên logic trong bảng sau.
Chỉ các trường trong danh mục của nghiệp vụ mới được nhận để ghi.

| Nghiệp vụ | Trường file → trường DB hoặc cách lấy giá trị |
| --- | --- |
| hr_employee | employee_code, full_name, date_of_birth, gender, email, phone, hire_date, employee_status → các cột cùng tên của employee; academic_degree, academic_title, research_field → lecturer; is_lecturer điều khiển tách bản ghi; employee_id DB sinh rồi truyền sang lecturer |
| hr_assignment | assignment_code → định danh nguồn; employee_code → employee_id; department_code → department_id; position_code → position_id; start_date, end_date, is_primary, assignment_status → assignment; assignment_id DB sinh |
| hr_qualification | employee_code → employee_id; qualification_name + qualification_type → qualification_id; institution, year_obtained → employee_qualification |
| admissions_registration | applicant_code → định danh nguồn; full_name, national_id, date_of_birth, gender, email, phone, high_school_name, province → applicant; program_code → program_id; year_label → admission_year_id; method_code → admission_method_id; preference_rank, application_date, admission_status → admission; applicant_id/admission_id DB sinh |
| academic_curriculum | program_code → program_id; curriculum_code, curriculum_name, version, effective_from, effective_to, status → curriculum; course_code → course_id; prerequisite_course_code → prerequisite_course_id; semester_no, course_type, is_required → curriculum_course; curriculum_id/link ID DB sinh |

Trường DB bắt buộc được kiểm tra theo schema và theo bản ghi sắp tạo. Ví dụ tạo
employee cần employee_code, full_name, employee_status; tạo admission cần đủ bốn FK,
preference_rank và admission_status. Default của DB như is_primary/is_required hoặc
created_at được dùng khi bỏ trống ở lần tạo mới. Một cột không xuất hiện trong file
không được tự thêm vào danh sách cập nhật.

Họ Và Tên/Họ Tên → full_name trong đúng nghiệp vụ; Mã NV/Mã nhân viên → employee_code.
Tên tiếng Việt không cần giống tên vật lý PostgreSQL. Các trường hợp sau cần quyết
định rõ qua field_mapping và ignored_columns:

- Cột chưa có tên tương đương được khai báo.
- Hai cột cùng khớp một trường. Phải chọn một cột và xác nhận bỏ qua cột còn lại.
- Cột bổ sung không dùng khi ghi. Cột này vẫn nằm trong Bronze.

Mapping phiên bản 1 là copy một cột cho một trường, kèm chuyển kiểu/từ điển đã khai
báo. Ghép Họ + Tên, lấy giá trị thay thế giữa nhiều cột và xóa giá trị có chủ đích cần
quy tắc biến đổi riêng; không tự đoán hoặc tự ghép trong luồng này.

## 4. Luồng xử lý

1. Upload kiểm tra quyền, dataset và cấu hình nghiệp vụ; lưu file gốc vào staging.
   HTTP 202 trả ingestion_job_id, chưa xác nhận dữ liệu đã ghi bảng nghiệp vụ.
2. Ingestion worker đọc/kiểm tra CSV hoặc Excel, giữ file gốc và bằng chứng phiên
   bản/checksum trong Bronze.
3. Silver worker nhận diện duy nhất một nghiệp vụ bằng các cột định danh được khai
   báo, hoặc dùng import_options đã gửi. Cột lạ/mơ hồ bị chặn và lưu báo cáo trong job.
4. Profile lưu mapping, thứ tự header, tùy chọn parser, phiên bản/fingerprint nghiệp
   vụ. Mã được giữ dạng chuỗi; ngày dùng định dạng tường minh. Không tự đoán mã bị
   Excel làm mất số 0 hoặc ngày 01/02 có nghĩa tháng/ngày nào.
5. Dữ liệu phân tích Silver và artifact nghiệp vụ được lưu riêng. Họ tên, email,
   điện thoại, ngày sinh, CCCD không đưa vào bảng phân tích của các contract mới.
   Mã nguồn vẫn được giữ để tra cứu và đếm thực thể theo phạm vi file/nguồn.
6. Gold tổng hợp và Gold chi tiết được ghi/đọc lại bằng đúng version và checksum.
7. Hồ sơ nhân viên, hồ sơ đăng ký tuyển sinh và khung chương trình có bước lập kế
   hoạch trong transaction chỉ đọc. Worker gom dòng theo khóa, tra mã danh mục theo
   lô, kiểm tra bản ghi đích và lưu bản xem trước cùng Gold sync job. Kế hoạch đầy đủ
   lưu trong MinIO, gắn phiên bản/checksum với artifact nguồn đã đọc. Kế hoạch chứa
   khóa, tham chiếu, thao tác dự kiến và vị trí dòng nguồn. Giá trị nghiệp vụ nằm
   trong artifact nguồn đã ghim. API trả số liệu theo bảng, tên trường cần ghi và
   vị trí dòng nguồn.
8. Luồng đồng bộ tự động kiểm tra lại quyền và lập lại kế hoạch trong transaction ghi.
   Nếu kế hoạch thay đổi sau lúc chuẩn bị, lần ghi bị chặn với `IMPORT_PLAN_STALE`.
   Khi hợp lệ, ghi cha trước con, dùng PK do DB sinh cho FK của con, rồi ghi định danh,
   audit và kết quả trong cùng transaction. Chỉ đánh dấu SYNCED khi mọi bảng hoàn tất.

Gold SUCCEEDED và database SYNCED là hai trạng thái khác nhau. Khi danh mục bị thiếu,
bản Gold tổng hợp có thể đã được công bố nhưng bước ghi bảng nghiệp vụ là BLOCKED.
Ứng dụng phải xem database_committed hoặc database_status để báo kết quả ghi DB.
Không dùng record_count hoặc HTTP 202 để suy ra số hồ sơ đã được lưu.

File không khớp một nghiệp vụ vẫn có thể được lưu/phân tích theo luồng generic hiện
có. Đồng bộ DB bị chặn nếu thiếu quy định đích; đã bỏ nhánh từng chỉ lấy course từ
file bảng điểm phẳng rồi báo đồng bộ xong.

## 5. API cho tích hợp

### Danh mục và kiểm tra header

`GET /api/v1/ingestion/import-types` trả nghiệp vụ thuộc domain được phép tải.

`POST /api/v1/ingestion/imports/analyze` nhận JSON:

```json
{
  "domain": "hr",
  "columns": ["Mã nội bộ", "Tên cán bộ", "Là giảng viên", "Trạng thái nhân viên", "Ghi chú"],
  "options": {
    "import_type": "hr_employee",
    "version": "1",
    "field_mapping": {"employee_code": "Mã nội bộ", "full_name": "Tên cán bộ"},
    "ignored_columns": ["Ghi chú"]
  }
}
```

HTTP 200 xác nhận ánh xạ header; chưa kiểm tra dữ liệu dòng và tham chiếu DB.
HTTP 422 trả issues để người dùng chọn lại cột hoặc bổ sung cột còn thiếu.

### Nạp file

Giữ `POST /api/v1/ingestion/uploads` với các trường multipart hiện có. Có thể thêm
`import_options` là chuỗi JSON chứa options bên trên. `date_format` nhận `%Y-%m-%d`,
`%d/%m/%Y` hoặc `%m/%d/%Y`. Mặc định ISO. blank_update mặc định và chỉ nhận preserve.

`context` là các mã dùng chung cho lượt nhập. Tuyển sinh chấp nhận `program_code`,
`year_label`, `method_code`. Đào tạo chấp nhận `program_code`, `version`. Giá trị
ngữ cảnh được dùng khi file thiếu cột hoặc ô để trống. Nếu file có giá trị khác,
bước chuẩn bị báo `IMPORT_CONTEXT_MISMATCH` tại dòng và cột nguồn. Upload ghim
phiên bản, fingerprint quy tắc và cấu hình ngữ cảnh vào job.

Không gửi import_options vẫn nhận diện được các file có đủ cột định danh của một
nghiệp vụ và tên tương đương đã khai báo. Ví dụ file hr_employee cần employee_code
và is_lecturer dưới một tên được chấp nhận. File tên lạ cần options tường minh.
Dataset đang có profile tự chạy tùy chỉnh phải dùng profile tương thích với
business_import; cấu hình xung đột bị chặn, không âm thầm bỏ yêu cầu nghiệp vụ.

### Theo dõi ghi DB

`GET /api/v1/ingestion/jobs/{ingestion_job_id}/business-import` dùng quyền đọc
ingestion hiện có. Trả bronze_status, silver_status, gold_status, database_status,
database_committed, error_report và database_result.

`GET /api/v1/ingestion/jobs/{ingestion_job_id}/business-import/preview` dùng cùng
phạm vi truy cập. Kết quả có `status` là `PENDING`, `READY`, `BLOCKED` hoặc
`NOT_AVAILABLE` và có trạng thái ghi DB riêng. Bản xem trước `READY` hiển thị
tổng số thao tác theo bảng và tối đa 100 thao tác, mỗi thao tác hiển thị tối đa 20
vị trí dòng. `READY` xác nhận bước chuẩn bị đã hoàn tất. Luồng hiện hành tự tiếp tục
đồng bộ khi kiểm tra trước ghi thành công. Sau khi xử lý lỗi tham chiếu hoặc kế hoạch
cũ, dùng endpoint retry database-sync của Gold để lập kế hoạch mới.

Ví dụ kết quả:

```json
{
  "database_status": "SYNCED",
  "database_committed": true,
  "database_result": {
    "entities": {"employee": 1, "lecturer": 1},
    "tables": {
      "employee": {"inserted": 1, "updated": 0, "unchanged": 0},
      "lecturer": {"inserted": 1, "updated": 0, "unchanged": 0}
    },
    "input_rows": 1,
    "atomic": true
  }
}
```

Lỗi dữ liệu/quan hệ có code, entity, field, column và locator gốc (sheet/dòng theo
định dạng reader). Xung đột nhiều dòng có previous_locators. Trả tối đa 200 lỗi cùng
issue_count tổng, không đưa giá trị cá nhân vào báo cáo lỗi API. Sửa file và nạp lại,
hoặc sau khi bổ sung danh mục, quản trị dùng endpoint retry database-sync hiện có.

## 6. Gold và lịch sử

Các contract mới có chiều thống kê theo mã chương trình/năm/phương thức, trạng thái
hoặc học vị tương ứng. Với tuyển sinh, distinct_applicant_count phân biệt số thí
sinh và registration_count phân biệt số đăng ký. Với khung, có curriculum_count
và curriculum_course_count. Các chỉ số này đếm trong một scope/snapshot nguồn,
không được cộng rồi coi là số người duy nhất trên toàn bộ hệ thống.

`app.business_import_change` lưu hành động inserted/updated/unchanged, khóa đích,
trường thay đổi, giá trị trước/sau, nguồn và locator. API trạng thái không trả các
giá trị cá nhân trong audit. Chưa thêm màn hình/API đọc chi tiết hồ sơ kết hợp cho
Manager; các API Gold/Manager hiện có tiếp tục áp dụng phạm vi dữ liệu đã có.

## 7. Triển khai và kiểm chứng

Migration `20261118_business_imports.sql` thêm bảng audit, chỉ mục/FK đến Gold job và
đặt phiên bản tổng hợp mặc định cho job mới thành group-counts-v2. Worker vẫn xử lý
job group-counts-v1 bằng fingerprint và phép đếm cũ, không đổi job đang chờ hoặc
lịch sử. Migration được đăng ký vào upgrade.psql. Chạy chuỗi nâng cấp DB theo quy trình
`make db-upgrade` của dự án trước khi bật worker sử dụng contract mới.

Migration `20261118_business_import_preparation.sql` bổ sung metadata kế hoạch
vào Gold sync job. Áp dụng cùng chuỗi `make db-upgrade` trước khi cập nhật API
và Gold worker. Các job cũ vẫn đọc được. Job đồng bộ đã kết thúc trước khi nâng cấp
có trạng thái xem trước `NOT_AVAILABLE`.

Các file ví dụ nằm ở `data/samples/imports/`. Mã chương trình, môn, năm và phương
thức trong file ví dụ phải tồn tại trong DB thực tế trước khi nạp. Đây là dữ liệu
minh họa, không phải danh mục được hệ thống tự tạo.

Kiểm thử mới gồm mapping tiếng Việt, cột mơ hồ/lạ, kiểu ngày/mã, cha–con, quan hệ
nhiều–nhiều, thiếu mã, xung đột unique, nguồn khác, rollback và nhập lại. E2E dùng
database riêng, đi qua API CSV/Excel và các worker đến PostgreSQL, kiểm tra artifact
phân tích không chứa các trường cá nhân và kiểm tra quyền đọc báo cáo.

Nghiệm thu bằng file PNS thực tế vẫn cần một file phòng ban cung cấp và danh mục
tham chiếu tương ứng. Không thay thế bước đối chiếu từng trường bằng HTTP 202.

Kiểm chứng ngày 18/11/2026: 291 kiểm thử hồi quy và 25 kiểm thử E2E qua; 4 test
migration bật bằng cờ riêng không chạy trong lượt pytest thường. Đã chạy entrypoint
upgrade.psql hai lần trên DB trống riêng để kiểm tra khởi tạo và tính lặp lại.
Ruff và Mypy cho bốn module mới không còn lỗi. Sau lần chỉnh chuẩn hóa cuối,
84 test profile/cleaning/import và 8 E2E nhập nghiệp vụ tiếp tục qua.

Migration đã áp dụng vào DB local. API /health trả 200, OpenAPI có các route mới,
API và ba worker ingestion/Silver/Gold đã khởi động. Các phép thử ghi dùng DB riêng;
không nạp dữ liệu mẫu vào hồ sơ nghiệp vụ local của người dùng.
