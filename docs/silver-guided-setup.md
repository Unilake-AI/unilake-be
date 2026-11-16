# Thiết lập cách xử lý Silver từ dữ liệu Bronze

Cập nhật: 15/11/2026. Luồng mặc định giữ nguyên mã nguồn, không yêu cầu bản đối chiếu mã.

Phòng ban tải file → kiểm tra và lưu Bronze → hệ thống gợi ý → Data Admin xem
trước và xác nhận → làm sạch, chuẩn hóa → lưu Parquet vào Silver. Các phép hợp
nhất mã giữa nguồn chỉ chạy khi được chọn riêng.

## Hai cách xử lý

| Cách xử lý | `identity_mode` | Mã nguồn | Cần bản đối chiếu? |
| --- | --- | --- | --- |
| Giữ mã nguồn, mặc định trên giao diện | `source` | `007` vẫn là chuỗi `007`; khác chuỗi `7` | Không |
| Hợp nhất mã giữa các nguồn, nâng cao | `canonical` | Lấy mã chung theo quan hệ đã được xác nhận | Có |

Các cấu hình đã lưu trước thay đổi này mặc định vẫn là `canonical`. Mã kiểm chứng
của chúng giữ nguyên để có thể đọc lại dữ liệu, lịch sử và bằng chứng phê duyệt.
Chỉ những cấu hình mới xác nhận trong chế độ giữ nguồn mới dùng `source`.

## Thao tác trên FE

1. ADMIN/DATA_ADMIN vào `/data/cleaning`, chọn lần nạp đã lưu Bronze thành công.
2. Hệ thống xác minh đúng phiên bản file, mã kiểm tra nội dung, kích thước và số dòng.
3. Mặc định chọn **Giữ mã nguồn — làm sạch và chuẩn hóa cơ bản**.
4. Có thể chọn mẫu nhân viên/chức vụ/... hoặc **Theo cấu trúc file**. Khi chưa có
   mẫu khớp duy nhất, hệ thống tự đề xuất cấu trúc theo các cột thực tế.
5. Kiểm tra ghép cột, ngày tháng, giá trị được coi là trống, khoảng trắng và việc
   gộp dòng. Chỉ gợi ý, không tự xác nhận thay người dùng.
6. Với cấu trúc theo file, chọn kiểu lưu từng cột, đánh dấu cột mã cần giữ nguyên,
   cột bắt buộc có giá trị và cột phân biệt đối tượng nếu có. Không bắt buộc tự đặt
   khóa nghiệp vụ; khi chưa có khóa, mỗi dòng vẫn có mã truy vết riêng.
7. Xác nhận những cột không sử dụng. Các quy định bảo vệ trường cá nhân có sẵn
   vẫn áp dụng; dùng cấu trúc file không tự mở quyền lưu các cột này.
8. Bấm **Xem trước cách xử lý**, kiểm tra số dòng và dữ liệu mẫu.
9. Bấm **Xác nhận và lưu cách xử lý**, sau đó chạy làm sạch và lưu Silver.

Trong chế độ `source`, không hiện yêu cầu chọn bản đối chiếu hoặc nguồn mã liên
kết. Chuyển sang `canonical` mới hiện các lựa chọn nâng cao này. Chế độ nâng cao
vẫn dùng các mẫu đã khai báo và kiểm tra mã chung như trước.

STAFF tiếp tục upload và sửa file nguồn. Quyền xem cấu hình, xác nhận và công bố
Silver vẫn thuộc ADMIN/DATA_ADMIN; việc phê duyệt công bố trong môi trường yêu cầu
phê duyệt vẫn được kiểm tra riêng.

## API của màn hình

Mọi đường dẫn bắt đầu bằng `/api/v1`, xác thực bằng Bearer token.

| API | Dùng khi | Ghi dữ liệu? |
| --- | --- | --- |
| `GET /pipeline/cleaning/ingestion-jobs/{job_id}/setup` | Đọc file và lấy gợi ý | Chỉ đọc |
| `POST /pipeline/cleaning/ingestion-jobs/{job_id}/setup/preview?limit=20` | Xem trước lựa chọn chưa lưu | Chỉ xử lý trong bộ nhớ |
| `POST /pipeline/cleaning/ingestion-jobs/{job_id}/setup/confirm` | Xác nhận kết quả đã xem | Lưu cấu hình và lịch sử |
| `GET /pipeline/profiles` | Đọc các cách xử lý đã lưu | Chỉ đọc |
| `POST /pipeline/cleaning/ingestion-jobs/{job_id}/preview` | Xem trước bằng cấu hình đã lưu | Chỉ xử lý trong bộ nhớ |
| `POST /pipeline/cleaning/ingestion-jobs/{job_id}` | Chạy làm sạch và lưu Silver | Lưu lịch sử, báo cáo, cách ly lỗi và công bố khi đạt |

`setup` trả thêm một lựa chọn `entities[].custom=true`, có tên `file_{dataset_id}`,
chứa các cột nguồn đã được đối chiếu với quy định bảo vệ dữ liệu. Tên cột không
phù hợp được đặt tên lưu rõ ràng và vẫn giữ `field_mapping` về tên gốc. FE không
cần tự đặt tên loại dữ liệu hoặc đoán cấu trúc.

Body xem trước theo mẫu chức vụ:

```json
{
  "analysis_fingerprint": "<lấy từ GET setup>",
  "identity_mode": "source",
  "entity": "position",
  "field_mapping": {
    "position_id": "position_id",
    "position_code": "position_code",
    "position_name": "position_name",
    "position_level": "position_level"
  },
  "ignored_columns": [],
  "date_formats": {},
  "null_tokens": [""],
  "trim_text": false,
  "collapse_exact_duplicates": false,
  "crosswalk_release": null,
  "reference_namespaces": {}
}
```

Body xem trước file chưa có mẫu (ví dụ dataset 31):

```json
{
  "analysis_fingerprint": "<lấy từ GET setup>",
  "identity_mode": "source",
  "entity": "file_31",
  "field_mapping": {"tracking_code": "tracking_code", "day": "day", "amount": "amount"},
  "field_types": {"tracking_code": "string", "day": "date", "amount": "decimal"},
  "identifier_fields": ["tracking_code"],
  "required_fields": [],
  "primary_key": [],
  "date_formats": {"day": "DMY"},
  "ignored_columns": [],
  "null_tokens": [""],
  "trim_text": false,
  "collapse_exact_duplicates": false,
  "crosswalk_release": null,
  "reference_namespaces": {}
}
```

`field_mapping` có chiều trường đích → cột nguồn. Các khóa tùy chọn theo cột là tên
trường đích. `field_types` hỗ trợ `string`, `integer`, `decimal`, `date`, `timestamp`,
`boolean`; decimal dùng 38 chữ số với tối đa 10 chữ số sau dấu chấm và không âm thầm
làm tròn giá trị vượt giới hạn. Cột mã hoặc khóa phân biệt đối tượng phải là chuỗi.
Các trường riêng `field_types`, `required_fields`, `primary_key` dùng với cấu trúc
file; các mẫu có sẵn giữ các quy định kiểu và cột bắt buộc đã khai báo.

Ngày dùng `YMD` (2026-02-01), `DMY` (01/02/2026) hoặc `MDY` (02/01/2026).
Ngày mơ hồ không được tự chọn cách hiểu. Ngày giờ vẫn cần có múi giờ rõ ràng.
Kiểu gợi ý là lựa chọn ban đầu để xem lại, chưa phải quyết định áp dụng.

Body xác nhận:

```text
{ "choices": <đúng lựa chọn đã xem trước>, "confirmation_token": <token từ preview> }
```

Trong chế độ `source`, preview xử lý đầy đủ cả làm sạch và chuẩn hóa kiểu dù không
có đối chiếu, rồi cấp token xác nhận. Không còn lưu hoặc dùng tên bản đối chiếu
thay thế. Nếu `canonical` chưa chọn đối chiếu, chỉ xem phần làm sạch và không cấp
token để xác nhận chế độ nâng cao đó.

Token được ký riêng, hiệu lực 30 phút, gắn người xem, job, bản Bronze, cấu trúc,
lựa chọn và lần sửa cấu hình. Sửa lựa chọn hoặc cấu hình thì cần xem trước lại.
FE chỉ thông báo đã lưu Silver khi kết quả chạy có trạng thái `PUBLISHED` hoặc
`PUBLISHED_WITH_QUARANTINE`; lưu cấu hình thành công chưa công bố Silver.

## Dữ liệu và truy vết

- Silver giữ kiểu Parquet thống nhất theo cấu trúc được xác nhận.
- Mã `007` được lưu dạng chuỗi; không ép sang số 7. Mã liên kết tới bảng khác
  vẫn là mã nguồn, không bắt buộc tải sẵn bảng cha hoặc hợp nhất mã để làm sạch cơ bản.
- Ô trống không tự được điền. Việc bỏ khoảng trắng và gộp dòng cần được chọn rõ.
- Khi có khóa được chọn, xung đột cùng mã được phát hiện. Khi chưa có khóa,
  hệ thống phát hiện dòng trùng hoàn toàn; mặc định giữ dòng và báo phát hiện,
  chỉ gộp khi người dùng bật lựa chọn đó.
- Khi có dòng lỗi mà chưa khai báo khóa, hiện chặn công bố một phần để tránh gộp
  nhầm với dữ liệu cũ. Có thể sửa nguồn hoặc xác nhận cột phân biệt đối tượng.
- Mỗi dòng có `_source_record_id` và các trường nguồn, lô, lần xử lý, thời gian,
  phiên bản cấu trúc và quy định. Bản kê còn giữ phiên bản Bronze, checksum và vị
  trí dòng thực tế. Bước giữ mã được ghi là `SOURCE_IDENTIFIER_PRESERVATION`;
  không ghi nhận đã hợp nhất định danh khi chưa chạy bước đó.
- Cấu hình giữ nguồn dùng phạm vi riêng theo dataset, nguồn và cấu trúc, tránh
  trộn mã giống nhau của những nguồn khác nhau hoặc lẫn với mã chung đã hợp nhất.

## Dùng lại và chạy tự động

### Khi nút làm sạch chưa bật

File đã vào Bronze nhưng chưa có cách xử lý khớp nguồn, tên file và các cột thì
cần xem trước và xác nhận cách xử lý trước. Giao diện liệt kê rõ lựa chọn còn
thiếu, như cách đọc ngày hoặc cột chưa được xác nhận bỏ qua. File có ngày sai có
thể không gợi ý được một cách đọc ngày duy nhất; người phụ trách vẫn chọn cách
đọc phù hợp với nguồn để tách các giá trị sai ra khi xử lý. Không cần sửa hết
dòng lỗi mới được xem trước hoặc lưu cấu hình.

Ví dụ file kiểm thử `hr_staff_employees_errors.xlsx`: chọn mẫu `employee`, giữ
mã nguồn, chọn `YMD` cho `hire_date` và `birth_year` (lấy từ `date_of_birth`),
xác nhận bỏ qua `full_name`, `email`, `phone`. Bật bỏ khoảng trắng và gộp trùng,
sau đó bấm **Xem trước cách xử lý → Xác nhận và lưu cách xử lý**. Nút chạy được
bật sau khi đã lưu. Với 24 dòng kiểm thử, kết quả xem trước là 9 đạt, 14 cách ly
và 1 gộp trùng. Cấu hình đúng không có nghĩa tất cả dòng dữ liệu đều đạt.

FE ưu tiên tự chọn đúng một cấu hình `source` khớp nguồn, bộ dữ liệu, tên file và
các cột. Cấu hình `canonical` cũ vẫn dùng được khi được chọn rõ. Thay đổi cấu trúc
cần xem lại cách xử lý. Lưu cấu hình không tự bật chính sách chạy tự động.

Sau khi kiểm tra thủ công có thể dùng cấu hình `source` hoặc `canonical` cho
Auto Pipeline. Silver Worker đọc đúng chế độ đã lưu trong chính sách và giữ
phiên bản quy định của job. Xem [hướng dẫn tự chạy](auto-silver-pipeline.md).

## Kiểm chứng và triển khai

Các kiểm thử bao gồm CSV và Excel, giữ `007` khác `7`, không đọc đối chiếu trong
chế độ giữ nguồn, file chưa có mẫu, ngày mơ hồ, dòng trùng có ô trống, cách ly dữ
liệu lỗi, token xác nhận, phân quyền và khả năng đọc cấu hình cũ.

Kiểm thử local thật dùng PostgreSQL database riêng và MinIO: upload → ingestion
worker → gợi ý → xem trước → xác nhận → lưu Parquet → đọc lại → kiểm tra nguồn gốc.
Cấu hình giữ nguồn cũng được kiểm tra qua hàng đợi và Silver Worker.

```bash
.venv/bin/pytest -q --no-cov tests/test_cleaning_setup.py
AUTO_PIPELINE_LIVE_TEST=1 .venv/bin/pytest -q --no-cov tests/test_auto_pipeline_live.py
```

Không có bảng/cột SQL mới cho thay đổi này. Chế độ và cấu trúc nằm trong phần
cấu hình JSON đã có; không đổi cấu hình cũ hoặc dữ liệu Silver đã công bố. Backend
chạy dev reload và FE dev sẽ nạp mã mới. Nếu worker chạy Docker từ image cũ, cần
nạp lại mã trước khi thử tự chạy; worker phải dùng cùng phiên bản code với API.
