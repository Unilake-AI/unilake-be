# Tải file từ máy người dùng vào Bronze

Ngày cập nhật: 10/11/2026.

## 1. Mục đích và phạm vi

`POST /api/v1/ingestion/uploads` nhận nội dung file trực tiếp từ máy người dùng. Người dùng chọn bộ dữ liệu Bronze đã có hoặc nhập tên để tạo bộ mới trong cùng yêu cầu. STAFF không phải nhập mã dataset, nguồn hay hệ thống nguồn khi tạo mới; backend tự cấp các mã và chuẩn bị nguồn upload.

Hỗ trợ CSV, Excel `.xlsx` và `.xlsm`. File phải có dòng tiêu đề. Bản đầu tiên yêu cầu tên file không dấu, chỉ gồm chữ, số, dấu gạch ngang, gạch dưới và dấu chấm, tối đa 200 ký tự; không nhận đường dẫn con. Không hỗ trợ `.xls`, PDF, Word hoặc file nén tùy ý.

`ADMIN` và `DATA_ADMIN` được tải file vào mọi bộ dữ liệu Bronze hợp lệ. STAFF chỉ tải/tạo trong lĩnh vực được cấp: `HR_STAFF` → `hr`, `ACADEMIC_STAFF` → `academic`, `ADMISSIONS_STAFF` → `admissions`. Với bộ đã có, lấy lĩnh vực từ dataset, không cho thay đổi qua form. Với bộ mới, tự lấy từ role nếu chỉ có một lĩnh vực; quản trị hoặc tài khoản nhiều lĩnh vực phải chọn `domain`, backend kiểm tra lại quyền. `VIEWER` và role `*_MANAGER` không có thêm role upload vẫn không được tải hoặc tạo bộ qua API này.

Phạm vi hiện vẫn theo ba lĩnh vực, chưa phải từng đơn vị con. Nguồn upload được quản lý chung theo lĩnh vực (`UPLOAD_HR`, `UPLOAD_ACADEMIC`, `UPLOAD_ADMISSIONS`). Khi tạo nguồn lần đầu, `owner_department_id` chưa được gán; backend không đoán đơn vị từ tên file hoặc tạo đơn vị giả. Điều kiện phân công đơn vị của chức năng giao sửa lỗi vẫn là phần cần thống nhất riêng.

## 2. Luồng xử lý

```mermaid
flowchart TD
    A[Chọn file và chọn bộ đã có hoặc đặt tên bộ mới] --> B[Kiểm tra quyền, dung lượng và chuẩn bị bộ dữ liệu]
    B --> C[Lưu file vào kho tiếp nhận riêng trên MinIO]
    C --> D[Đọc lại để xác minh file đã lưu đầy đủ]
    D --> E[Ghi job vào PostgreSQL và trả HTTP 202]
    E --> F[Worker lấy job và đọc đúng phiên bản file]
    F --> G[Kiểm tra toàn bộ cấu trúc và các giới hạn]
    G -->|Đạt yêu cầu| H[Lưu nguyên bản vào Bronze và xác minh lại]
    G -->|Không đạt| I[Lưu báo cáo lỗi và đánh dấu FAILED]
    H --> J[Ghi nhận SUCCESS và nguồn gốc dữ liệu]
    J --> K[Có thể gọi luồng làm sạch và Silver riêng]
```

Kho tiếp nhận mặc định là `ingestion-uploads`, tách biệt với Bronze, Silver và Gold. File nằm trong kho này chưa được công nhận là dữ liệu Bronze. Chỉ kết quả job `SUCCESS` mới xác nhận đã lưu Bronze thành công.

API tạo khóa riêng cho mỗi lần tải lên: `incoming/{dataset_id}/{mã-lần-tải}/{tên-file}`. Vì vậy, hai người tải cùng tên file không ghi đè nhau. Sau khi lưu, API đọc lại đúng phiên bản để đối chiếu kích thước và mã kiểm tra nội dung. Chỉ khi file và job đều được lưu thành công mới trả HTTP 202.

Bộ dữ liệu mới, nguồn và job được lưu chính thức cùng nhau, sau khi file đã lưu và xác minh ở kho tiếp nhận. Lỗi tiếp nhận hoặc lưu job sẽ hủy các đăng ký vừa tạo. Sau `202`, dataset được giữ lại dù worker phát hiện file không hợp lệ: người dùng chọn lại bộ đó để nạp bản đã sửa.

Worker dùng chung hàng đợi, cơ chế giữ job, gia hạn và cập nhật kết quả với ingestion hiện có. Job tải lên có `job_type=UPLOAD_V1`; nguồn đã đăng ký vẫn dùng `EXECUTION_V1`.

## 3. So sánh hai API

| Nội dung | `/ingestion/jobs` | `/ingestion/uploads` |
|---|---|---|
| Đầu vào | JSON chỉ định nguồn và file/bảng đã có | File, tên bộ mới hoặc lựa chọn bộ đã có, tùy chọn đọc |
| Cần đăng ký vị trí nguồn | Có | Không; backend tự tạo nguồn tải lên |
| Thu thập PostgreSQL | Có | Không |
| Tạo job và xử lý bằng worker | Có | Có |
| Kiểm tra và lưu bản gốc vào Bronze | Có | Có |
| Theo dõi bằng API job | Có | Có |
| Tự động làm sạch và chạy Silver | Chưa có | Chưa có |

Nguồn tải lên được dùng lại theo bộ dữ liệu và loại file, với tên nội bộ `UPLOAD_{dataset_id}_{CSV hoặc EXCEL}`. Nguồn này không có đường dẫn server và không chứa thông tin kết nối. Không thể dùng `/ingestion/jobs` để yêu cầu đọc tùy ý từ nguồn tải lên; phải gửi file qua `/ingestion/uploads`.

## 4. Cách gọi API

### STAFF tạo bộ dữ liệu mới và tải CSV

Đăng nhập bằng `HR_STAFF`, chỉ cần tên bộ dữ liệu và file:

```sh
curl -X POST 'http://localhost:8000/api/v1/ingestion/uploads' \
  -H "Authorization: Bearer $TOKEN" \
  -F 'dataset_name=Danh sách nhân viên' \
  -F 'description=Dữ liệu nhân viên kỳ hiện tại' \
  -F 'file=@/duong-dan/hr_employees_20000_valid.csv'
```

Không gửi `dataset_id`, `source_system_id` hay `data_source_id`. Lĩnh vực tự là `hr`. Nếu là `ADMIN`/`DATA_ADMIN`, hoặc có nhiều role STAFF khác lĩnh vực, gửi thêm `domain=hr` hoặc một lĩnh vực khác được phép. Nhập tên trùng trong nguồn upload của cùng lĩnh vực trả `409 UPLOAD_DATASET_NAME_CONFLICT`, không tự nối vào bộ cũ. Backend chuẩn hóa khoảng trắng và Unicode của tên, kiểm tra trùng không phân biệt hoa/thường. Hãy gọi `GET /api/v1/datasets` và chọn lại bộ có sẵn nếu muốn nạp tiếp.

### Nạp tiếp vào bộ dữ liệu đã chọn

```sh
curl -X POST 'http://localhost:8000/api/v1/ingestion/uploads' \
  -H "Authorization: Bearer $TOKEN" \
  -F 'dataset_id=7' \
  -F 'file=@/duong-dan/employees.csv'
```

Mặc định CSV là UTF-8, phân cách bằng dấu phẩy, có dòng tiêu đề, dấu nháy kép bao giá trị và không bỏ dòng đầu. Các giá trị được giữ nguyên trong file gốc; chưa chuyển kiểu hoặc làm sạch ở bước này.

### CSV có cách đọc riêng

```sh
curl -X POST 'http://localhost:8000/api/v1/ingestion/uploads' \
  -H "Authorization: Bearer $TOKEN" \
  -F 'dataset_id=7' \
  -F 'parser_options={"delimiter":";","encoding":"utf-8","skip_rows":0}' \
  -F 'file=@/duong-dan/employees.csv'
```

### Excel

```sh
curl -X POST 'http://localhost:8000/api/v1/ingestion/uploads' \
  -H "Authorization: Bearer $TOKEN" \
  -F 'dataset_id=7' \
  -F 'parser_options={"sheet_name":"NhanSu","header_row":1,"skip_rows":0}' \
  -F 'file=@/duong-dan/employees.xlsx'
```

Không truyền `sheet_name` thì worker chọn sheet đầu tiên và ghi lại tên thực tế vào kết quả. Một lần tải lên chỉ xử lý một sheet. File Excel nguyên bản vẫn được giữ đầy đủ trong Bronze, gồm cả các sheet khác.

Trên Swagger, chọn `POST /api/v1/ingestion/uploads`, bấm **Try it out** và chọn file. Tạo mới: nhập `dataset_name`, bỏ trống `dataset_id`; tài khoản một lĩnh vực bỏ trống `domain`. Nạp vào bộ cũ: chỉ gửi `dataset_id`, không gửi `dataset_name`, `description`, `domain`. Gửi lẫn hai lựa chọn bị từ chối với `422 UPLOAD_DESTINATION_INVALID`. `parser_options` là tùy chọn cho cả hai. Trên UI thật, danh sách hiển thị tên và tự gửi ID khi người dùng chọn bộ cũ. Không tự đặt HTTP `Content-Type` khi dùng công cụ gửi form; để công cụ tạo thông tin phân tách multipart.

API trả HTTP 202 với `ingestion_job_id`, `job_type=UPLOAD_V1`, `status=QUEUED`, `data_source_id`, `dataset_id` và tên file trong `source_table`. HTTP 202 nghĩa là đã tiếp nhận để xử lý, chưa có nghĩa file đã vào Bronze.

`ADMIN`, `DATA_ADMIN` xem được toàn bộ job ingestion. STAFF chỉ xem được các job thuộc lĩnh vực được cấp, kể cả khi đoán được mã job của lĩnh vực khác. `VIEWER` không được đọc ingestion.

Theo dõi bằng:

```text
GET /api/v1/ingestion/jobs/{ingestion_job_id}
GET /api/v1/ingestion/jobs?data_source_id={data_source_id}
```

Khi thành công, `output` chứa URI Bronze, phiên bản tệp, mã SHA-256, kích thước, cách đọc thực tế và nguồn gốc từ lần tải lên. `validation_report` cho biết kết quả kiểm tra. API không trả thông tin xác thực kho tiếp nhận.

## 5. Kiểm tra trước khi vào Bronze

API kiểm tra loại file, tên file, cách đọc và dung lượng thực tế trong lúc sao chép từng phần. File rỗng bị từ chối. MIME do máy người dùng gửi không được dùng làm bằng chứng file hợp lệ.

Worker đọc đúng phiên bản đã ghim, kiểm tra lại kích thước và SHA-256, sau đó quét toàn bộ phần dữ liệu được chọn:

- Dòng tiêu đề phải tồn tại, không có tên cột rỗng hoặc trùng.
- Các dòng phải có cấu trúc phù hợp với dòng tiêu đề.
- File không chỉ có tiêu đề mà không có dữ liệu.
- CSV phải đọc được theo bảng mã và dấu phân cách đã chọn.
- Excel phải có sheet được yêu cầu; ô công thức, ô lỗi và dữ liệu không có tiêu đề bị chặn.
- Giới hạn dung lượng, số dòng, số cột, độ dài ô, thời gian xử lý và kích thước giải nén Excel được áp dụng.

File mới chưa có cấu trúc đích khai báo: worker kiểm tra cấu trúc thực tế của file, không tự kết luận cột nào là mã nhân sự hay cột nào mang ý nghĩa nghiệp vụ. Bước này không sửa dữ liệu thiếu, xóa trùng, đổi tên cột hoặc tính chỉ số. Những việc đó thuộc làm sạch, chuẩn hóa và Gold ở các bước tiếp theo.

Giới hạn mặc định:

| Biến môi trường | Mặc định |
|---|---:|
| `UPLOAD_MAX_BYTES` | 50 MiB |
| `UPLOAD_MAX_RECORDS` | 1.000.000 dòng |
| `UPLOAD_MAX_COLUMNS` | 500 cột |
| `UPLOAD_MAX_CELL_CHARS` | 1.000.000 ký tự/ô |
| `UPLOAD_TIMEOUT_SECONDS` | 60 giây cho từng giai đoạn được kiểm soát |
| `UPLOAD_MAX_EXPANDED_BYTES` | 200 MiB cho tổng nội dung giải nén Excel |

Nên đặt giới hạn request tại reverse proxy tương ứng, có chừa phần thông tin multipart. Giới hạn file trong ứng dụng được kiểm tra khi đọc `UploadFile`; framework có thể đã ghi request vào file tạm trước khi endpoint chạy.

## 6. Cài đặt trên môi trường đang chạy

### PostgreSQL

Áp dụng migration sau bằng quy trình triển khai database hiện có, trước khi chạy API/worker mới:

```text
infra/postgres/migrations/20261109_ingestion_uploads.sql
```

Migration thêm cột `upload_input` và chỉ mục hàng đợi cho loại job mới; không xóa hoặc sửa kết quả job cũ. Database khởi tạo mới đã có phần này trong `infra/postgres/init/02-schema.sql`.

Thay đổi tạo dataset trong lần upload đầu không cần migration mới; dùng các bảng và ràng buộc đã có. Tầng `BRONZE` phải được thiết lập trước. Hệ thống tự tạo nguồn upload khi cần, không cần seed một dataset mẫu.

### MinIO và tài khoản dịch vụ

Kho `MINIO_BUCKET_UPLOADS` phải riêng biệt và bật quản lý phiên bản. Compose đã bổ sung khởi tạo kho này; với hệ thống đã chạy, thực hiện bước quản trị dưới đây bằng tài khoản quản trị trên máy triển khai:

```sh
.venv/bin/python -m app.infra.minio.upload_accounts plan
.venv/bin/python -m app.infra.minio.upload_accounts prepare
.venv/bin/python -m app.infra.minio.upload_accounts apply
.venv/bin/python -m app.infra.minio.upload_accounts export-runtime
```

`prepare` tạo `.env.upload-accounts.local` với quyền đọc/ghi giới hạn cho chủ file; không ghi đè file đang có. `apply` tạo kho, bật phiên bản và tạo hai tài khoản mới, không phải lệnh dùng để xoay thông tin xác thực của tài khoản đã tồn tại.

`export-runtime` tách thành:

- `.env.upload-backend.local`: tài khoản ghi/đọc lại trong kho tiếp nhận, không có quyền ghi Bronze.
- `.env.upload-worker.local`: tài khoản chỉ đọc kho tiếp nhận. Worker tiếp tục dùng tài khoản ghi/xác minh Bronze hiện có.

Compose nạp đúng file cho từng dịch vụ. Nếu chạy Python trực tiếp, xuất các biến trong file tương ứng vào môi trường của tiến trình trước khi khởi động; ứng dụng không tự nạp cả hai bộ tài khoản tải lên. Không đưa các file chứa thông tin xác thực vào kho mã nguồn.

Cần khởi động lại API và worker sau khi cấu hình, không chỉ cập nhật API. Thiếu tài khoản riêng hoặc kho chưa bật phiên bản thì việc tiếp nhận bị chặn; không dùng tài khoản quản trị để chạy thay.

## 7. Lỗi và phục hồi

| Tình huống | Kết quả |
|---|---|
| Không đủ quyền | HTTP 401/403; không tạo job |
| Thiếu file hoặc chưa chọn bộ cũ/đặt tên bộ mới | HTTP 422 |
| Trộn hai lựa chọn bộ cũ/bộ mới | HTTP 422 `UPLOAD_DESTINATION_INVALID` |
| Tạo mới nhưng chưa chọn lĩnh vực khi tài khoản có nhiều lựa chọn | HTTP 422 `UPLOAD_DOMAIN_REQUIRED` |
| Tạo mới ngoài lĩnh vực được cấp | HTTP 403 `UPLOAD_DOMAIN_FORBIDDEN` |
| Tên bộ mới trùng trong nguồn upload cùng lĩnh vực | HTTP 409 `UPLOAD_DATASET_NAME_CONFLICT` |
| Tầng Bronze chưa có / nguồn upload bị tắt hoặc mã nguồn bị dùng cho loại khác | HTTP 422; không tự bật lại hoặc sửa cấu hình quản trị |
| Bộ dữ liệu không tồn tại | HTTP 404 |
| Bộ dữ liệu không phải Bronze, bị tắt hoặc thiếu hệ thống nguồn | HTTP 422 |
| Loại file không hỗ trợ | HTTP 415 |
| Tên file, cách đọc hoặc file rỗng không hợp lệ | HTTP 422 |
| File vượt giới hạn tiếp nhận | HTTP 413; không tạo job |
| Lỗi lưu MinIO hoặc ghi job | HTTP 503; không báo đã tiếp nhận thành công |
| Nội dung file không đạt kiểm tra | Job `FAILED`, có báo cáo lỗi; không ghi Bronze |
| Worker hết thời hạn giữ job | Job `FAILED` với `WORKER_LEASE_EXPIRED`, không công bố kết quả muộn |

MinIO và PostgreSQL không có một lần lưu chung. Nếu file đã nằm trong kho tiếp nhận nhưng ghi job thất bại hoặc request bị hủy, có thể còn file không gắn với job. File đó không được worker tự nhận và không được xem là Bronze. Chưa có tác vụ tự động dọn các file này; không đặt thời hạn xóa ngắn có thể làm mất file của job còn chờ.

Retry thủ công, auto retry và lịch sử các lần chạy đã được triển khai trong [hướng dẫn vận hành ingestion](ingestion-reliability.md). File sai nội dung phải được sửa rồi nạp lại vào cùng dataset. Các lần tải cùng tên file có vị trí và phiên bản riêng. Chưa có khóa chống gửi trùng cho upload; kiểm tra trùng tên bộ mới chỉ ngăn tạo thêm dataset, không bảo đảm toàn bộ request chỉ chạy một lần.

Để không tạo nguồn hoặc bộ trùng khi có hai yêu cầu đồng thời, backend giữ lượt tạo mới theo từng lĩnh vực cho đến khi lưu job hoặc hủy yêu cầu. Điều này làm các lần tạo bộ mới cùng lĩnh vực chờ nhau trong thời gian tiếp nhận file. Kho tiếp nhận và PostgreSQL vẫn không có một lần lưu chung; file mồ côi khi database lỗi vẫn là giới hạn nêu trên.

## 8. Đi tiếp sang Silver

Sau khi job tải lên đạt `SUCCESS`, API làm sạch hiện có có thể nhận `UPLOAD_V1` và dùng đúng bản Bronze vừa lưu:

```text
POST /api/v1/pipeline/cleaning/ingestion-jobs/{ingestion_job_id}
```

Bước Silver vẫn cần cấu hình tương ứng với `data_source_id`, `dataset_id`, tên file và cấu trúc dữ liệu của nguồn. Upload không tự tạo cấu hình Silver hoặc tự chạy Gold. Xem [luồng nguồn thật → Silver](standardization/source-silver-flow.md).

## 9. Các tệp triển khai và kiểm thử

- `app/modules/ingestion/router.py`: nhận multipart và trả lỗi API có kiểm soát.
- `app/modules/ingestion/uploads.py`: kiểm tra tiếp nhận, tự tạo nguồn, lưu kho tiếp nhận và tạo job.
- `app/modules/ingestion/upload_capture.py`: xác minh đúng file và kiểm tra toàn bộ dữ liệu trong worker.
- `app/repositories/upload_repo.py`: tìm bộ cũ hoặc tạo bộ mới; tự cấp mã, quản lý nguồn upload, kiểm tra tên trùng; chỉ lưu chính thức cùng job.
- `app/modules/ingestion/execution.py`, `app/ingestion_worker.py`, `app/repositories/ingestion_repo.py`: dùng chung hàng đợi, gia hạn và lưu Bronze.
- `app/infra/minio/upload_accounts.py`: chuẩn bị tài khoản riêng theo dịch vụ.

```sh
.venv/bin/python -m pytest -q tests/test_upload_new_dataset.py tests/test_ingestion_uploads.py tests/test_upload_accounts.py tests/test_ingestion_execution.py tests/test_source_cleaning.py
```

Kiểm thử dùng CSV/Excel thật và mô phỏng MinIO trong bộ nhớ; có kiểm tra nguyên bản file, lỗi cấu trúc, sai phiên bản, SHA-256, quyền truy cập, dung lượng, lỗi kho lưu trữ và lỗi ghi job. Phần tự tạo/tái sử dụng nguồn được kiểm tra bằng SQL thật trên SQLite. Kiểm thử này không chứng minh PostgreSQL/MinIO đang vận hành đã được cấu hình hoặc migration thành công.

`tests/test_upload_new_dataset.py` kiểm tra lần tải đầu của ba role STAFF, chọn lại bộ đã tạo, dùng chung hệ thống nguồn, chặn tên trùng, gửi sai phạm vi, chọn lĩnh vực cho quản trị/tài khoản nhiều role, hủy đăng ký khi lỗi kho lưu trữ hoặc lưu job, và mô tả hai lựa chọn trên Swagger. SQLite không xác minh cơ chế khóa đồng thời của PostgreSQL; cần kiểm tra trên môi trường tích hợp khi triển khai.
