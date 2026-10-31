# Quy tắc làm sạch dữ liệu — Admissions, Academic/Training và HR

| Thuộc tính | Giá trị |
|---|---|
| Phiên bản | 0.1.0-draft |
| Trạng thái | DRAFT — chưa được domain owner phê duyệt để chạy production |
| Phạm vi | Làm sạch các bản ghi được parse từ object Bronze trước khi chuyển sang Standardization |
| Nguồn schema | Admissions 1.3.0; Academic 1.2.0; HR 1.2.0 |
| Chủ sở hữu | Data Administrator phối hợp domain owner từng domain |

## 1. Mục đích và ranh giới

Tài liệu này thống nhất cách **phát hiện, xử lý và ghi nhận** missing values,
duplicate records, định dạng không thống nhất và giá trị không hợp lệ cho ba
domain. Đây là hợp đồng rule để các subtask triển khai cleaning dùng chung.

Input lúc chạy là record có cấu trúc được parser đọc từ **đúng object version
Bronze**. Cùng với record, pipeline cần giữ tối thiểu domain, entity, source
system/namespace, batch/run, object key/version/checksum và locator của record.
CSV locator là ordinal record; Excel dùng sheet/dòng vật lý; snapshot DB cần
locator ổn định tương ứng. Bronze giữ nguyên dữ liệu nguồn; cleaning không sửa
hay ghi đè Bronze.

Cleaning chỉ áp dụng các phép biến đổi được khai báo trong profile nguồn đã
duyệt. Mapping tên cột, chuyển kiểu target, crosswalk ID, chuẩn hóa định danh
và công bố Silver thuộc bước Standardization; không suy đoán chúng trong
cleaning. Rule nghiệp vụ liên entity được kiểm tra ở bước thích hợp sau khi
đã có mapping/reference cần thiết, không giả vờ xử lý được chỉ từ một file.

Schema hiện hành là căn cứ về cấu trúc, nullable, khóa, kiểu, enum và constraint,
nhưng một số DQ rule còn cần duyệt nghiệp vụ. Vì vậy nội dung bên dưới là **đề
xuất triển khai**, không thay thế phê duyệt schema/rule hoặc gate Silver hiện có.

## 2. Thứ tự áp dụng

1. Pin input Bronze object version/checksum và parser/profile version; không đọc
   "object mới nhất" thay cho object được giao.
2. Kiểm tra entity/schema/header. Thiếu cột bắt buộc hoặc binding entity không
   rõ ràng là lỗi batch; không đoán entity từ tên file hoặc tự ghép header gần
   giống.
3. Phân biệt field không có trong schema đầu vào với field có giá trị null.
   Chỉ parse theo encoding, delimiter, locale, null token và format đã khai
   báo cho profile nguồn.
4. Áp dụng biến đổi định dạng an toàn đã duyệt, rồi kiểm tra missing, kiểu,
   pattern, enum, range, khóa và các ràng buộc có đủ dữ liệu để kiểm tra.
5. Ghi outcome và issue có rule ID/version, entity, field, locator, severity,
   batch/run. Không ghi raw value hoặc PII vào log/issue thường.
6. Chỉ chuyển các record đạt điều kiện downstream; record lỗi được cách ly và
   chuyển issue, không âm thầm loại bỏ. Silver snapshot chỉ được publish theo
   contract/gate của bước sau.

## 3. Quy tắc nền tảng

| Rule ID | Phát hiện | Hành động đề xuất | Severity / lưu ý |
|---|---|---|---|
| CLN-STR-01 | Chuỗi có khác biệt Unicode tương đương hoặc khoảng trắng ngoài đầu/cuối | Chuẩn hóa Unicode NFC; trim đầu/cuối chỉ cho field/profile được khai báo. Không đổi dấu, không xóa khoảng trắng bên trong, không uppercase hàng loạt, không cắt chuỗi | Warning nếu profile chưa khai báo; lỗi độ dài/pattern sau phép chuẩn hóa là record quarantine |
| CLN-NULL-01 | Giá trị bằng null token của profile nguồn | Chuyển thành null nội bộ. Baseline CSV của schema hiện tại khai báo ô trống `""`; token như `N/A`, `NULL`, `-`, `0` không tự động được coi là null | Chỉ dùng token đã pin theo source + field |
| CLN-NULL-02 | Field target nullable có giá trị null | Giữ null; không điền trung bình, mode, chuỗi mặc định, ngày hiện tại hoặc số 0 | Null có thể có nghĩa nghiệp vụ; tôn trọng schema |
| CLN-NULL-03 | Field bắt buộc null/blank sau khi áp dụng null token | Quarantine record, không tự bịa giá trị. Thiếu cả cột bắt buộc là lỗi batch | ERROR |
| CLN-DUP-01 | Trùng PK/composite PK trong cùng namespace + entity | Nhóm các record theo khóa nguồn ổn định. Nếu mọi field nghiệp vụ giống hệt sau phép chuẩn hóa đã duyệt, chỉ emit một bản ghi và giữ locator của mọi bản sao; nếu payload khác nhau, quarantine toàn bộ nhóm khóa, không chọn tùy tiện dòng đầu/cuối | ERROR; cần domain owner duyệt chính sách gộp exact replay |
| CLN-DUP-02 | Vi phạm unique/unique_together hoặc business key được schema khai báo | Phát hiện va chạm, quarantine các record liên quan để xử lý; không tự xóa bản ghi hoặc tự merge người/sự kiện | ERROR; unique nullable chỉ so sánh khi giá trị khóa không null |
| CLN-DUP-03 | Bản ghi giống nhau do tên, ngày sinh hoặc thuộc tính gần giống, nhưng không có stable key/crosswalk | Không tự deduplicate/fuzzy match; giữ record và tạo cảnh báo/review có kiểm soát nếu rule nghiệp vụ yêu cầu | Không sử dụng PII làm khóa dedupe |
| CLN-FMT-01 | Ngày, số thập phân, boolean hoặc enum không theo format profile | Parse/chuyển đổi chỉ khi format/dictionary nguồn được pin. Không đoán locale, timezone hoặc ý nghĩa alias | Lỗi parse/alias chưa khai báo: quarantine |
| CLN-VAL-01 | Sai type, length, pattern, enum, min/max hoặc ngày không tồn tại | Không sửa thành giá trị mặc định và không cắt/ép kiểu mất dữ liệu; quarantine record, phát issue theo field | ERROR |
| CLN-REF-01 | FK không resolve được hoặc trỏ tới nhiều bản ghi | Giữ nguyên nguồn trong Bronze; quarantine record cần reference, phát issue; không đổi thành null hoặc tự tạo entity cha | ERROR; cần snapshot/reference đầy đủ |
| CLN-ISS-01 | Issue phát sinh trong cleaning | Ghi rule ID/version, domain/entity/field, opaque locator, input version, batch/run, code/severity và disposition | Không ghi raw value, tên, email, số điện thoại hoặc CCCD vào log thường |
| CLN-REPLAY-01 | Cùng input object version, parser/profile và rule version được chạy lại | Kết quả cleaning phải idempotent; không nhân đôi output hoặc issue chỉ vì retry | Retry/run ID vẫn phải được truy vết |

### Quy ước kết quả

| Outcome | Ý nghĩa |
|---|---|
| `PASS` | Record đạt các rule áp dụng và có thể chuyển bước tiếp theo |
| `PASS_WITH_WARNING` | Record được giữ, nhưng có warning/review không chặn theo policy đã duyệt |
| `QUARANTINE` | Record không được chuyển tiếp; cần sửa nguồn hoặc xử lý có thẩm quyền |
| `REJECT_BATCH` | Không thể xác định schema/profile/input batch an toàn; dừng batch |

Mặc định đề xuất: lỗi dữ liệu ở một record không làm mất các record khác; tuy
nhiên lỗi khóa, xung đột duplicate, cấu trúc đầu vào hoặc constraint snapshot
có thể chặn publish ở bước sau. Ngưỡng chặn cuối cùng phải đồng nhất với
standardization/Silver gate đã phê duyệt.

## 4. Quy tắc theo field và nhóm field

| Nhóm field | Xử lý missing | Định dạng / invalid |
|---|---|---|
| PK, mã bắt buộc, FK bắt buộc | Null/blank → `QUARANTINE`; thiếu cột trong cấu trúc nguồn → `REJECT_BATCH` | Mã giữ dạng string, giữ số 0 đầu. Sai pattern hoặc FK unresolved → `QUARANTINE`; không tự sinh ID/crosswalk |
| Field nullable trong schema | Null được giữ nguyên, trừ khi rule nghiệp vụ duyệt thêm điều kiện | Giá trị có mặt nhưng sai type/format vẫn là lỗi, không đổi thành null |
| Text/name/description | Chỉ giữ null khi nullable; không dùng placeholder như `"Unknown"` | UTF-8/NFC; trim chỉ theo source profile. Không xóa dấu, không fuzzy merge tên, không truncate |
| Enum/status/boolean | Null theo nullable; field required không được thiếu | Chấp nhận giá trị canonical hoặc alias trong dictionary được duyệt và versioned. Alias lạ → `QUARANTINE` |
| Date/timestamp | Giữ null nếu nullable; không tự gán ngày hiện tại | Chỉ nhận format đã pin; ngày lịch không tồn tại → `QUARANTINE`. Timestamp naive cần timezone nguồn được xác nhận |
| Decimal/numeric | Null theo nullable; không thay bằng 0 | Parse chính xác theo separator profile, không đi qua float. Ngoài precision/scale/range → `QUARANTINE` |
| PII | Không suy diễn, điền hoặc ghi raw value vào issue/log thông thường | Chỉ đối chiếu trong vùng hạn chế nếu rule được duyệt; PII projection thuộc governance/Standardization |
| Audit/provenance | Thiếu metadata bắt buộc của handoff → dừng/quarantine theo contract; không chế lineage | Chỉ dùng metadata từ ingestion/handoff đáng tin cậy; locator phải opaque trong log thường |

## 5. Phạm vi entity và tiêu chí duplicate

Các PK/composite key bên dưới lấy từ schema domain đã ghi trong metadata. Đây
là tiêu chí **phát hiện khóa trùng**, không phải tuyên bố rằng hai record có
cùng nội dung luôn được phép xóa. Với xung đột cùng khóa, áp dụng `CLN-DUP-01`
và không chọn winner tự động.

| Domain | Entity | PK nguồn | Unique/composite key bổ sung trong schema | Lưu ý xử lý |
|---|---|---|---|---|
| Admissions | `major` | `major_id` | `major_code` | `department_id` bắt buộc; tên gần giống không phải duplicate |
| Admissions | `program` | `program_id` | `program_code` | Không gộp chương trình khác mã chỉ vì tên giống |
| Admissions | `admission_method` | `admission_method_id` | `method_code` | `description` nullable; không suy ra method từ description |
| Admissions | `admission_year` | `admission_year_id` | `year_label` | Kiểm tra `end_date > start_date`; không tự sửa ngày |
| Admissions | `applicant` | `applicant_id` | `national_id` nullable unique | Null CCCD hợp lệ theo schema; không fuzzy match theo PII |
| Admissions | `admission` | `admission_id` | (`applicant_id`, `admission_year_id`, `preference_rank`); (`applicant_id`, `program_id`, `admission_year_id`, `admission_method_id`) | Một applicant có nhiều nguyện vọng là hợp lệ; không dedupe chỉ theo applicant |
| Academic/Training | `student` | `student_id` | `student_code`; `applicant_id` nullable unique; `admission_id` nullable unique | Hai FK nullable có thể cùng null với sinh viên nguồn cũ |
| Academic/Training | `course` | `course_id` | `course_code` | `credit_hours` theo miền schema |
| Academic/Training | `curriculum` | `curriculum_id` | `curriculum_code`; (`program_id`, `version`) | Không gộp các version chương trình khung |
| Academic/Training | `curriculum_course` | `curriculum_course_id` | (`curriculum_id`, `course_id`) | Prerequisite không được trùng chính course; rule học kỳ cần kiểm tra theo schema |
| Academic/Training | `semester` | `semester_id` | `semester_code` | Không chồng lấn và academic year phải khớp code theo DQ schema |
| Academic/Training | `class` | `class_id` | `class_code` | Null `max_capacity`/`room` có ý nghĩa theo schema; không điền mặc định |
| Academic/Training | `class_lecturer` | `class_lecturer_id` | (`class_id`, `lecturer_id`) | Mỗi lớp đúng một dòng primary theo DQ rule; assignments khác lecturer là hợp lệ |
| Academic/Training | `enrollment` | `enrollment_id` | (`student_id`, `class_id`) | Sinh viên học lại ở học kỳ khác có thể hợp lệ; kiểm tra trùng học phần/học kỳ theo rule nghiệp vụ |
| Academic/Training | `enrollment_grade` | `enrollment_grade_id` | `enrollment_id` | Điểm có thể null khi chưa có kết quả; không đổi null thành 0 |
| HR | `department` | `department_id` | `department_code` | `parent_department_id` null có nghĩa đơn vị gốc; kiểm tra cây và cycle |
| HR | `position` | `position_id` | `position_code` | Không hợp nhất position theo tên gần giống |
| HR | `qualification` | `qualification_id` | DQ rule `HR-DQ-13`: `qualification_name` không trùng | Schema không đặt UNIQUE; collision cần review/cách ly theo rule nghiệp vụ |
| HR | `employee` | `employee_id` | `employee_code`; `email` nullable unique | Email/phone/date_of_birth/hire_date có thể null; không định danh người bằng tên |
| HR | `lecturer` | `employee_id` | PK cũng là FK tới `employee.employee_id` | Một employee không có dòng lecturer không mặc nhiên là lỗi |
| HR | `employee_qualification` | (`employee_id`, `qualification_id`) | Trùng composite PK | `institution`/`year_obtained` nullable; hai văn bằng khác qualification là hợp lệ |
| HR | `assignment` | `assignment_id` | Không có unique key bổ sung khai báo trong schema | Nhiều assignment của một employee là hợp lệ; kiểm tra overlap/status theo rule nghiệp vụ, không dedupe theo employee |

### Xử lý khi khóa bị trùng

1. Khóa được so sánh trong phạm vi namespace nguồn + entity; ID số trùng giữa
   hai source system không chứng minh cùng một thực thể.
2. Cùng khóa và cùng payload sau các biến đổi đã được duyệt: có thể collapse
   thành một record output duy nhất sau khi domain owner phê duyệt; giữ danh
   sách locator của mọi source row để audit.
3. Cùng khóa nhưng khác payload: quarantine mọi record của khóa đó; không tự
   chọn "bản mới nhất" trừ khi có authority/freshness policy được duyệt cho
   source cụ thể.
4. Trùng unique/business key nhưng PK khác nhau: quarantine các record va
   chạm và yêu cầu xử lý có thẩm quyền. Không merge record hoặc đổi key tự động.
5. Không có stable key: không xóa record bằng exact-row heuristic hoặc fuzzy
   matching trong phiên bản rule này.

## 6. Ràng buộc nghiệp vụ sẵn có cần được giữ

Schema chứa các `quality_rules` theo domain. Cleaning cần phát hiện/ghi nhận
những lỗi có đủ dữ liệu để kiểm tra, nhưng không tự sửa các quan hệ nghiệp vụ
phức tạp. Tối thiểu cần giữ các nhóm rule sau làm validation gate downstream:

- **Admissions:** khóa/FK/field constraints; ngày admission year; application
  date trong mùa; admission score tương quan method/max score/status; preference
  rank và unique tuple; quy tắc hồ sơ Accepted/student.
- **Academic/Training:** khóa/FK/field constraints; khoảng thời gian semester và
  curriculum; prerequisite; một primary lecturer; duplicate enrollment; capacity;
  công thức/quy đổi điểm và tính đầy đủ điểm theo trạng thái học kỳ.
- **HR:** khóa/FK/field constraints; cây department và loại đơn vị; hire date,
  assignment intervals/status/primary; quy tắc chức vụ và qualification.

Các ID trong schema hiện tại (`ADM-DQ-*`, `ACA-DQ-*`, `HR-DQ-*`) được giữ làm
tham chiếu, không đổi severity hoặc ý nghĩa trong tài liệu cleaning. Rule tuổi,
khớp PII, authority của nguồn và các rule cần ngày chốt phải được xử lý trong
vùng/policy phù hợp; không đưa raw PII vào Silver issue/log thông thường.

## 7. Ghi nhận issue và lineage

Mỗi issue tối thiểu có:

```text
rule_id, rule_version, domain, entity, field (nếu có),
issue_code, severity, disposition, opaque_record_locator,
source_system/namespace, batch_id, run_id,
bronze_object_key, object_version_id, checksum, parser_profile_version
```

Không chứa raw value trong issue thường. Nếu cần kiểm tra giá trị nhạy cảm,
phải dùng quy trình restricted access được governance phê duyệt. Record
quarantine vẫn phải truy ngược được tới đúng Bronze version và locator.

Các số liệu run nên phân biệt `records_read`, `records_passed`,
`records_quarantined`, `records_rejected`, `issues_by_rule` và số record output.
Không tính record bị bỏ âm thầm là thành công; mọi mismatch count phải giải
thích được bằng disposition.

## 8. Quyết định còn chờ domain owner

Các mục dưới đây ngăn việc bật rule production; owner cần điền tên/ngày và kết
quả duyệt trước khi đổi trạng thái tài liệu sang `APPROVED`.

| ID | Cần xác nhận | Đề xuất tạm thời | Owner/trạng thái |
|---|---|---|---|
| APR-01 | Danh sách null token theo source + field; đặc biệt `N/A`, `NULL`, `-`, chuỗi trắng | Chỉ token khai báo trong profile; baseline CSV coi ô trống là null | Admissions / Academic / HR — PENDING |
| APR-02 | Có được trim khoảng trắng đầu/cuối trên code/name hay không | Chỉ trim field được khai báo; không sửa khoảng trắng bên trong | Ba domain — PENDING |
| APR-03 | Với cùng PK và payload giống hệt, có cho collapse không; locator/lineage nào phải giữ | Chỉ collapse exact payload trong cùng namespace sau phê duyệt | Ba domain — PENDING |
| APR-04 | Với trùng unique key nhưng PK khác nhau, hành động và authority chọn winner | Quarantine mọi record xung đột; không tự chọn winner | Ba domain — PENDING |
| APR-05 | Enum alias nguồn tiếng Việt/legacy và dictionary version tương ứng | Không map alias chưa được duyệt | Ba domain — PENDING |
| APR-06 | Source authority/freshness policy cho bản ghi được cập nhật và replay | Không chọn theo timestamp nếu chưa có policy | Data Governance + domain owner — PENDING |
| APR-07 | Timezone cho timestamp nguồn naive và timestamp format mỗi source | Không suy từ timezone máy; chặn profile chưa khai báo | Ba domain — PENDING |
| APR-08 | Ngưỡng lỗi `QUARANTINE` so với chặn publish toàn snapshot | Record issue không được emit; snapshot gate theo policy Silver đã duyệt | Data Governance — PENDING |
| APR-09 | Policy PII cho identity matching, issue access và retention | Không dùng PII để fuzzy dedupe; issue thường không chứa PII | Data Governance — PENDING |
| APR-10 | Các domain quality rules hiện có đã được owner duyệt/version chưa | Giữ nguyên làm tham chiếu, không nâng thành cleaning auto-fix | Ba domain — PENDING |

### Biên bản phê duyệt

| Domain | Owner | Phiên bản schema/profile được duyệt | Ngày | Trạng thái |
|---|---|---|---|---|
| Admissions | Chưa chỉ định | Schema 1.3.0; cleaning 0.1.0-draft | — | PENDING |
| Academic/Training | Chưa chỉ định | Schema 1.2.0; cleaning 0.1.0-draft | — | PENDING |
| Human Resources | Chưa chỉ định | Schema 1.2.0; cleaning 0.1.0-draft | — | PENDING |

## 9. Mapping từ rule sang các subtask triển khai

| Subtask | Rule chính |
|---|---|
| Missing Values | `CLN-NULL-01` đến `CLN-NULL-03`; field nullable/required theo schema và entity matrix |
| Duplicate Records | `CLN-DUP-01` đến `CLN-DUP-03`; PK/unique keys theo entity matrix |
| Định dạng và giá trị không hợp lệ | `CLN-STR-01`, `CLN-FMT-01`, `CLN-VAL-01`, `CLN-REF-01` |
| Logging, kiểm thử, downstream handoff | `CLN-ISS-01`, `CLN-REPLAY-01`, outcomes và lineage ở mục 7 |

### Trạng thái thành phần Missing Values

`backend/app/modules/pipeline/missing_values.py` triển khai thành phần đọc object
Bronze đã pin version rồi parse CSV hoặc XLSX và áp dụng rule missing theo
source-column profile. Thành phần yêu cầu SHA-256 và số dòng kỳ vọng từ job
ingestion; giữ provenance/locator, trả riêng record đạt, record quarantine,
issue không chứa raw value và thống kê thiếu theo field. Null tokens là chính
xác theo cấu hình; trim khi so token phải bật rõ trên từng field. Chỉ `.csv`
và `.xlsx` thuộc phạm vi thành phần này; không bao gồm `.xlsm` hoặc Parquet.

Thành phần này được gọi bởi orchestration chung được mô tả ở mục 12. Không
thay thế ingestion trigger; rule vẫn ở trạng thái draft cho tới khi domain
owner phê duyệt.

## 10. Trạng thái thành phần Duplicate Records

`backend/app/modules/pipeline/duplicate_records.py` triển khai thành phần độc
lập để phát hiện va chạm theo primary key, unique key, composite unique key và
business key được cấu hình. Profile có thể được tạo từ schema domain nhưng
phải có mapping tường minh từ tên field target sang tên cột nguồn; business key
ngoài schema phải được khai báo rõ trong profile. Giá trị khóa null/blank được
bỏ qua tại thành phần này để Missing Values xử lý theo rule riêng.

Thành phần đọc CSV/XLSX từ object Bronze có version, SHA-256 và số dòng đã pin;
giữ locator CSV hoặc sheet/row của Excel và provenance. Va chạm khóa với payload
khác nhau, hoặc unique/business key va chạm, sẽ quarantine toàn bộ các record
liên quan và ghi issue không chứa giá trị khóa/raw data. Exact duplicate theo
primary key mặc định cũng bị quarantine; chỉ được collapse khi profile bật
tường minh `collapse_exact_primary_key_duplicates`, đồng thời giữ locator của
tất cả source row. Không fuzzy match, không dedupe theo tên/PII và không tự
chọn bản ghi thắng.

Thành phần này được gọi sau Missing Values và Format/Invalid Values trong
orchestration ở mục 12. Rule/profile vẫn ở trạng thái draft cho tới khi domain
owner phê duyệt chính sách khóa và collapse.

## 11. Trạng thái thành phần Format và Invalid Values

`backend/app/modules/pipeline/format_validation.py` triển khai component độc
lập cho các record CSV/XLSX đã parse từ object Bronze. `build_format_profile`
có thể lấy type, pattern, enum, length, precision/scale và min/max từ schema
domain; mọi tên cột nguồn phải được ánh xạ tường minh sang field schema. Chỉ
field có mapping mới được kiểm tra; mapped field mặc định phải có trong input,
trừ khi được khai báo là optional source field trong profile. Source format và
phép biến đổi như trim,
Unicode NFC, alias enum, decimal separator, date format, boolean token phải
được cấu hình rõ trong profile; component không đoán từ dữ liệu mẫu.

Date được parse theo danh sách format đã cho và xuất ISO `YYYY-MM-DD`;
Excel date cell có giá trị datetime lúc 00:00 được chuyển về date; datetime có
thành phần giờ khác 00:00 bị quarantine, không cắt mất giờ. Timestamp chỉ nhận
format có timezone tường minh và xuất UTC ISO; decimal được đổi sang dấu chấm
và scale schema mà không làm tròn. Integer được kiểm tra, giá trị numeric
integral từ Excel được canonicalize, còn chuỗi số giữ nguyên biểu diễn nguồn.
Boolean chỉ nhận token đã cấu hình và chuẩn hóa chuỗi thành `true`/`false`.
Giá trị invalid làm quarantine toàn record, giữ nguyên payload gốc, đồng thời
phát issue theo field/rule/version/locator không chứa raw value. Các record
quarantine không nhận một phần phép chuẩn hóa được phát hiện ở field khác.
Giá trị null/blank được để cho Missing Values xử lý; FK và validation liên
entity cần reference snapshot, nằm ngoài component.

Component độc lập đọc object đã pin và đối chiếu SHA-256/row count; trong
orchestration, component nhận BronzeBatch đã parse một lần và giữ nguyên
provenance/locator. Chỉ `.csv` và `.xlsx` thuộc phạm vi. Source profile và
alias vẫn phải được domain owner duyệt trước khi dùng production.

## 12. Trạng thái tích hợp Cleaning Pipeline

`backend/app/modules/pipeline/cleaning_pipeline.py` kết hợp ba component theo
thứ tự Missing Values → Format/Invalid Values → Duplicate Records trên cùng
BronzeBatch đã parse. Input bắt buộc có source system/entity binding, source
paths dạng cột phẳng, record locator và provenance Bronze với S3 URI,
object-version ID, SHA-256 cùng metadata `input_version` khớp. Pipeline chỉ
chấp nhận CSV/XLSX đã được ingestion/parser đưa về BronzeBatch; nó không tự
download hoặc parse lại object. Cấu trúc lồng nhau cần được flatten/parse theo
quy trình riêng trước khi gọi cleaning.

Batch đầu ra giữ nguyên input version, provenance, source paths và locator của
các record đạt. Record quarantine không được chuyển sang bước sau. Count
invariant được kiểm tra theo `records_read = records_passed +
records_quarantined + records_collapsed`. `CleaningReport` tổng hợp issue theo
từng bước và có thể gửi tới `CleaningReportSink` để caller lưu audit; lỗi cấu
hình/metadata hoặc lỗi sink được raise rõ ràng, không tạo report thành công giả.

`CleaningSilverPipeline` gọi Cleaning Pipeline trước `SilverPipeline`; chỉ
BronzeBatch đã làm sạch được đưa vào schema mapping → type/identity
standardization → publication Silver. Khi một batch có lỗi dữ liệu theo record,
pipeline chuyển phần record đạt và giữ report quarantine. Nếu lỗi cấu trúc
hoặc cấu hình batch, run dừng trước publication. Ingestion chưa tự kích hoạt
orchestration này; caller cần tạo `CleaningNormalizationTask` với các profile,
mapping và identity services đã được duyệt.

Orchestration dùng chung cho Admissions, Academic/Training và HR, với điều
kiện mỗi task cung cấp profile đúng domain/entity và mapping nguồn tường minh.
Đủ schema cho 22 entity đã được xác minh ở bước profile; chưa có bộ source
profile/mapping production được owner phê duyệt cho mọi nguồn. Pipeline không
thay thế việc phê duyệt đó và không trích xuất PDF/DOCX.

Để rehearsal Cleaning→Silver qua Swagger trên ba profile tổng hợp, xem
[hướng dẫn endpoint thủ công](../standardization/manual-live-demo.md#chạy-cleaning-qua-swagger).
Endpoint chỉ nhận ingestion job demo `DEMO_FLOW_*` thành công, yêu cầu role
`ADMIN`/`DATA_ADMIN`, crosswalk release `manual-ingestion-demo-v1` và Silver
writer service account riêng. Đây không phải luồng production hoặc API xử lý
file lớn.

## 13. Kiểm thử và cách chạy

Test tích hợp
`backend/tests/test_cleaning_domains.py` dựng BronzeBatch có provenance, chạy
cleaning → mapping → type/identity standardization → Silver publication bằng
fixture rehearsal, cho mỗi domain:

| Domain | Entity test | Kiểm tra chính |
| --- | --- | --- |
| Admissions | `admission_method` | Giữ giá trị null nullable và ghi issue thông tin |
| Academic/Training | `course` | Làm sạch, chuẩn hóa và publish bản ghi hợp lệ |
| HR | `position` | Làm sạch, chuẩn hóa và publish bản ghi hợp lệ |

Các test kiểm tra report/counts, không có quarantine dữ liệu hợp lệ, giữ
`batch_id`/source identity ở bản ghi Silver và publication có thể đọc lại.
Ngoài ra, mỗi domain có test riêng cho giá trị invalid bị quarantine và xung đột
primary key khiến cả hai record bị quarantine. Các ca missing, metadata/
provenance sai và handoff thất bại cũng được kiểm tra trong
`test_cleaning_pipeline.py` cùng các test component. Tất cả dùng fixture tổng
hợp; publication rehearsal không xác nhận schema/profile đã được domain owner
phê duyệt production.

Tại thư mục `backend/`, chạy nhóm test cleaning và handoff:

```bash
python -m pytest tests/test_cleaning_domains.py tests/test_cleaning_pipeline.py \
  tests/test_missing_values.py tests/test_duplicate_records.py \
  tests/test_format_validation.py tests/test_file_handoff.py \
  tests/test_reader_standardization_integration.py tests/test_silver_publication.py \
  -o addopts=''
```

Tùy chọn `-o addopts=''` bỏ ngưỡng coverage cấu hình cho toàn backend khi chỉ
chạy một tập test hẹp; CI/full suite vẫn áp dụng ngưỡng coverage chuẩn.
Lần xác minh cho nhóm lệnh trên: **135 test passed**.

## 14. Tài liệu nguồn

- [Admissions schema](../data-schema/admissions_v1.schema.json) và
  [Admissions dataset specification](../data-schema/admissions-dataset-v1.md)
- [Academic schema](../data-schema/academic_v1.schema.json) và
  [Academic dataset specification](../data-schema/academic-dataset-v1.md)
- [HR schema](../data-schema/hr_v1.schema.json) và
  [HR dataset specification](../data-schema/hr-dataset-v1.md)
- [Scope matrix](../standardization/scope-matrix.md)
- [Standardization rules (draft)](../standardization/rules.md)
- [Bronze → Silver integration and publication contract](../standardization/silver-integration-p5.md)
- [Ingestion handover](../udl-12-ingestion-handover.md)
