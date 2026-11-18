# Cập nhật kết quả xét tuyển và xác nhận nhập học — BE

Triển khai theo đề xuất đã được chủ dự án chấp thuận ngày 18/11/2026.
Frontend giữ nguyên. Các thao tác mới dùng API trong Swagger `/docs` hoặc client gọi BE.

## 1. Quy định đã thực thi

| Nghiệp vụ | Dữ liệu được ghi | Quy tắc |
| --- | --- | --- |
| admissions_results | admission_score, admission_status; tự đặt enrollment_status=PENDING khi mới trúng tuyển | Chỉ cập nhật đăng ký đã có; kiểm tra mã nguồn, bốn khóa tham chiếu, expected_revision và thang điểm |
| admissions_enrollment | enrollment_status, enrollment_date | Chỉ đăng ký ACCEPTED; cần ngày do file cung cấp khi xác nhận; không tạo student |
| RESULT_CORRECTION | Điểm/kết quả của đăng ký | ADMISSIONS_MANAGER hoặc ADMIN tạo yêu cầu có lý do, xem trước rồi áp dụng |
| ENROLLMENT_CORRECTION | Đính chính ngày xác nhận hoặc chuyển trạng thái được phép | Dùng yêu cầu riêng, đúng revision hiện tại |
| RESTORE_ENROLLMENT | Khôi phục WITHDRAWN về PENDING hoặc CONFIRMED | Manager/Admin phải nêu lý do; CONFIRMED phải cung cấp ngày xác nhận |
| RECONCILE_LEGACY | Xác định PENDING, CONFIRMED hoặc DECLINED cho hồ sơ cũ đang NULL | Chỉ đăng ký ACCEPTED đã được đối soát; không tự suy ra từ dữ liệu cũ |

STAFF, ADMIN và DATA_ADMIN có thể nộp hai loại file qua quyền upload hiện có.
File tuân thủ quy tắc STAFF; quyền đính chính kết quả đã chốt đi qua yêu cầu riêng.
DATA_ADMIN đơn lẻ không có quyền đính chính. ADMISSIONS_MANAGER bị giới hạn dataset
admissions do PTS đang hoạt động sở hữu. ADMIN có phạm vi quản trị trên admissions.
Quyền và trạng thái hoạt động của tài khoản được đọc lại từ DB ngay khi ghi Gold.

Chuyển kết quả của STAFF:

- APPLIED → WAITLISTED, ACCEPTED hoặc REJECTED.
- WAITLISTED → ACCEPTED hoặc REJECTED.
- Giữ nguyên kết quả hiện tại được phép. Sau ACCEPTED/REJECTED, STAFF không đổi điểm
  hoặc đảo kết quả; phải dùng yêu cầu đính chính.

Chuyển nhập học:

- PENDING → CONFIRMED hoặc DECLINED bởi STAFF.
- CONFIRMED → WITHDRAWN bởi Manager/Admin qua ENROLLMENT_CORRECTION.
- DECLINED → CONFIRMED bởi Manager/Admin qua ENROLLMENT_CORRECTION.
- WITHDRAWN → PENDING/CONFIRMED chỉ qua RESTORE_ENROLLMENT.
- NULL của dữ liệu cũ chỉ được xác định qua RECONCILE_LEGACY.

Khi rút hồ sơ, giữ ngày xác nhận cũ. Khôi phục CONFIRMED yêu cầu ngày xác nhận
tường minh. Không đảo kết quả ACCEPTED của hồ sơ đang CONFIRMED trước khi giải
quyết việc rút hồ sơ. Quyết định WITHDRAWN và ngày cũ được lưu để truy vết.

Điểm có tối đa hai chữ số thập phân, nằm trong [0, admission_method.max_score].
Phương thức max_score=0 không được ghi điểm, kể cả số 0. Với phương thức dùng điểm,
cần điểm khi công bố ACCEPTED/REJECTED. Hệ thống không tự tính đậu/rớt từ điểm.

## 2. Lấy mã nguồn và phiên bản đang có

```http
GET /api/v1/admissions/registrations?dataset_id=31&limit=100&offset=0
```

Trả applicant_code, program_code, year_label, method_code, điểm/kết quả, trạng thái
nhập học, ngày xác nhận và revision. Có thể lọc applicant_code và program_code.
Các ID trong ví dụ cần thay bằng ID dataset thực tế.

Tải CSV để cập nhật:

```http
GET /api/v1/admissions/registrations/export?dataset_id=31&workflow=results
GET /api/v1/admissions/registrations/export?dataset_id=31&workflow=enrollment
```

CSV dùng UTF-8 có BOM, kèm expected_revision. Mỗi trang tối đa 10.000 dòng; dùng
limit/offset để lấy tiếp. Header X-Total-Count cho biết tổng, X-Next-Offset cho biết
trang kế tiếp. Bản xuất nhập học chỉ gồm hồ sơ ACCEPTED có quyết định PENDING,
CONFIRMED hoặc DECLINED. Hồ sơ NULL/WITHDRAWN cần quy trình Manager tương ứng.

Danh sách và thao tác chỉ truy cập hồ sơ có ánh xạ applicant_code trong đúng
namespace nguồn của dataset. Không ghép người bằng họ tên hoặc CCCD.

## 3. Nộp file cập nhật qua luồng hiện có

```http
POST /api/v1/ingestion/uploads
Content-Type: multipart/form-data
```

Gửi dataset_id, file và import_options dạng chuỗi JSON:

```json
{"import_type":"admissions_results","version":"1"}
```

File kết quả:

```csv
applicant_code,program_code,year_label,method_code,expected_revision,admission_score,admission_status
TS001,CNTT,2026,THPT,1,25.50,ACCEPTED
```

File nhập học dùng `{"import_type":"admissions_enrollment","version":"1"}`:

```csv
applicant_code,program_code,year_label,method_code,expected_revision,enrollment_status,enrollment_date
TS001,CNTT,2026,THPT,2,CONFIRMED,2026-11-18
```

Hai nghiệp vụ cập nhật cần chọn import_type tường minh. Tên cột tiếng Việt được
chấp nhận theo GET /api/v1/ingestion/import-types. Có thể dùng field_mapping,
ignored_columns và date_format như các nghiệp vụ nhập nhiều bảng trước đó.
Ô trống giữ giá trị cũ; trường phải có giá trị để thực hiện chuyển trạng thái vẫn
được kiểm tra. Muốn xóa điểm sai của phương thức không dùng điểm, Manager dùng
yêu cầu RESULT_CORRECTION với admission_score=null và lý do.

Luồng tiếp nhận, Bronze, Silver, Gold giữ cơ chế đã có. BE kiểm tra toàn bộ kế hoạch
trước các câu lệnh ghi nghiệp vụ; phần kiểm tra phụ thuộc DB nằm trong transaction
đồng bộ. Một dòng sai mã, điểm, trạng thái hoặc revision làm chặn
cả file; các bảng danh mục, applicant và student không được tạo từ hai loại file này.

Theo dõi kết quả:

```http
GET /api/v1/ingestion/jobs/{ingestion_job_id}/business-import
```

HTTP 202 chỉ xác nhận tiếp nhận. database_status=SYNCED và database_committed=true
xác nhận transaction đã hoàn tất. Báo cáo có bảng, số lượng thêm/sửa/giữ nguyên,
lỗi theo dòng/cột và các revision đã đổi (tối đa 200 mục; lấy danh sách đầy đủ bằng
API registrations có phân trang).

## 4. Đính chính của Manager/Admin

Tạo yêu cầu và xem bản trước/sau dự kiến:

```http
POST /api/v1/admissions/corrections
```

```json
{
  "dataset_id": 31,
  "admission_id": 100,
  "expected_revision": 3,
  "operation": "RESULT_CORRECTION",
  "reason": "Điều chỉnh điểm theo biên bản đối chiếu của phòng tuyển sinh",
  "changes": {"admission_score": "26.50", "admission_status": "ACCEPTED"}
}
```

BE trả correction_id, trạng thái REQUESTED và result.preview. Bước này chưa đổi
admission. Có thể tự cung cấp correction_id dạng UUID để nhận lại cùng yêu cầu
khi gửi lại cùng payload. Nội dung yêu cầu đã tạo được giữ cố định.

```http
GET /api/v1/admissions/corrections/{correction_id}
POST /api/v1/admissions/corrections/{correction_id}/apply
```

Apply đọc lại quyền, phạm vi dataset, trạng thái và expected_revision; hợp lệ mới
ghi và đổi yêu cầu sang APPLIED. Gọi apply lại yêu cầu đã APPLIED trả biên nhận cũ,
không ghi lần hai. Một Manager đủ quyền có thể tạo và áp dụng; đề xuất đã duyệt
không đặt yêu cầu hai người phê duyệt khác nhau.

Nếu trạng thái đích là nhập học, changes chỉ gồm enrollment_status và enrollment_date.
RESTORE_ENROLLMENT và RECONCILE_LEGACY là các operation riêng, không dùng file STAFF.
Yêu cầu có revision cũ trả HTTP 409; đọc dữ liệu hiện tại và tạo yêu cầu mới sau đối soát.

## 5. Revision, nạp lại và lịch sử

`admission.revision` bắt đầu từ 1. Thay đổi thực sự tăng một lần; không đổi dữ liệu
thì giữ nguyên. Trigger DB áp dụng cho mọi bộ ghi, không cho tự đặt revision tùy ý.
Các bộ ghi trong BE kiểm tra expected_revision và cập nhật bằng điều kiện revision.
Bộ ghi admission generic cũ bị chặn để tránh bỏ qua các quy định mới. File đăng ký
ban đầu chỉ thay đổi admission hiện có khi có expected_revision hợp lệ.

Hai yêu cầu khác nhau cùng sửa một phiên bản: một yêu cầu ghi trước, yêu cầu còn lại
báo xung đột. Nộp lại đúng nội dung file và cấu hình đã ghi được giữ nguyên khi audit
chứng minh kết quả đó vẫn là phiên bản hiện tại. Nếu sau đó đã có đính chính mới,
file cũ không được dùng để ghi đè.

```http
GET /api/v1/admissions/registrations/{admission_id}/history?dataset_id=31
```

Lịch sử gồm phiên bản trước/sau, người thao tác, nghiệp vụ, nguồn Gold/dòng/cột
hoặc correction_id và lý do. `app.admission_event` ghi mọi thay đổi admission;
`app.admission_correction` giữ yêu cầu và kết quả áp dụng. Lệnh SQL quản trị trực tiếp
không có ngữ cảnh ứng dụng vẫn tăng revision và có audit UNATTRIBUTED. Không gán
người thao tác giả cho lệnh SQL đó.

Gold tiếp tục phản ánh snapshot của từng file. API admissions trả trạng thái DB
hiện tại, gồm cả đính chính sau lần công bố Gold. Không dùng số dòng Gold để suy ra
số hồ sơ đã nhập học hoặc tự tạo sinh viên.

## 6. Migration và vận hành

Migration `20261118_update_admissions_workflows.sql` thêm hai trường nhập học,
revision, CHECK, chỉ mục, hai bảng lịch sử/yêu cầu và trigger. Đã đăng ký vào
upgrade.psql. Dữ liệu cũ giữ enrollment_status/enrollment_date=NULL; không suy ra
quyết định nhập học và không tạo lịch sử giả cho thời điểm trước khi nâng cấp.

Triển khai môi trường khác bằng quy trình `make db-upgrade`, sau đó nạp lại API và
worker. Các file ví dụ ở `data/samples/imports/admissions_results_v1.csv` và
`admissions_enrollment_v1.csv` dùng mã minh họa; cần thay bằng dữ liệu đã có trong DB.

## 7. Thông tin còn cần để nghiệm thu bằng dữ liệu thực tế

Không cần duyệt lại quy tắc của hai nghiệp vụ: chỉ đạo triển khai đã chấp thuận đề
xuất. Để đối chiếu với công việc phòng PTS, cần:

1. Một file kết quả và một file xác nhận nhập học đang dùng thực tế, cùng ý nghĩa
   của các cột/tên trạng thái chưa có trong từ điển. Không tự suy đoán tiêu đề lạ.
2. Dataset/nguồn của bộ hồ sơ đăng ký ban đầu, cùng danh mục chương trình, năm,
   phương thức và mã thí sinh đã được nhập. File cập nhật phải cùng namespace đó.
3. Với dữ liệu cũ chưa có ánh xạ mã thí sinh, cần bảng đối chiếu mã nguồn → applicant_id
   có bằng chứng. Có bảng đối chiếu mới thực hiện bước ánh xạ riêng; không ghép theo tên.
4. Người có ADMISSIONS_MANAGER/ADMIN xác nhận những hồ sơ cũ NULL cần đối soát,
   kèm quyết định và ngày thực tế nếu xác nhận nhập học.

Những thông tin này phục vụ dữ liệu nghiệp vụ thực tế, không chặn việc triển khai
và kiểm thử BE bằng các database cô lập.

## 8. Kết quả kiểm chứng

Ngày 18/11/2026: lượt test thường toàn BE có 1.693 test qua và 113 test cần điều kiện
riêng được bỏ qua. Lượt E2E được bật riêng có 38 test qua trên PostgreSQL/MinIO,
bao gồm dữ liệu cũ trước migration, chạy lại migration, revision/audit từ bộ ghi DB,
hai yêu cầu sửa đồng thời, nạp lại, rollback nhiều dòng và thu hồi quyền người nạp.
Ruff sạch lỗi và Mypy qua cho 15 module trong phạm vi kiểm tra.

Entrypoint upgrade.psql đã chạy thành công hai lần trên DB riêng. Migration đã áp
dụng vào DB local và API cùng ba worker đã nạp lại. Các lượt thử ghi dữ liệu dùng
database cô lập; DB local không được nạp thêm hồ sơ tuyển sinh giả để kiểm thử.
