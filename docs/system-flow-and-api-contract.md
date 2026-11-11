# UniLake AI — Luồng hệ thống và cách sử dụng API

Ngày rà soát: **10/11/2026**. Phạm vi: mã nguồn backend trong repository này.

Tài liệu này tập hợp luồng đang có và phương án cần thống nhất trước khi phát triển tiếp. **“Hiện có” là hành vi đã đối chiếu với code; “Đề xuất” là thiết kế chưa triển khai, chưa được coi là đã chốt.** Việc API có trong code không chứng minh API đó đã được triển khai hoặc chạy thành công trên Render. Tài liệu không xác nhận dữ liệu đang có trong database thật.

**Cập nhật sau rà soát:** đã triển khai phần STAFF đặt tên bộ dữ liệu mới và tải file trong một yêu cầu. Backend tự cấp mã và xác định lĩnh vực theo quyền; không cần nhập ID kỹ thuật. Phân quyền theo từng đơn vị con, tự gắn profile và chuyển Silver sang job vẫn chưa triển khai.

**Cập nhật API STAFF:** đã thêm thông tin quyền/lựa chọn cho UI, chi tiết và tìm bộ dữ liệu, lọc lịch sử cá nhân, thống kê yêu cầu, upload bản sửa trong yêu cầu và hỗ trợ gửi xác nhận từ job `UPLOAD_V1`. Xem [hướng dẫn STAFF](staff-api.md) về toàn bộ luồng và điều kiện phân công đơn vị.

## 1. Mục tiêu và ranh giới các tầng

UniLake AI tiếp nhận dữ liệu của các phòng ban, giữ được bản nguồn, tạo lớp dữ liệu sạch và thống nhất, rồi dùng lớp đó để xây dữ liệu phân tích và trả lời câu hỏi.

| Thành phần | Lưu hoặc làm gì | Không nên hiểu thành |
| --- | --- | --- |
| Kho tiếp nhận | Giữ file vừa tải lên trong lúc chờ kiểm tra | File đã được chấp nhận vào Bronze |
| Bronze | Giữ bản dữ liệu nguồn đã thu thập, kèm bằng chứng về nội dung và phiên bản | Dữ liệu đã sạch hoặc đã đúng cấu trúc Silver |
| Silver | Làm sạch, đổi tên cột, thống nhất kiểu và mã, kiểm tra cấu trúc và liên kết | Nơi tính toàn bộ chỉ số, báo cáo nghiệp vụ |
| Gold | Tổ chức dữ liệu phục vụ phân tích, tổng hợp và tính chỉ số theo nghiệp vụ | Chức năng đã hoàn thiện chỉ vì đã có bucket Gold |
| Catalog | Mô tả dữ liệu: bảng, cột, ý nghĩa, nơi sử dụng | Chính các file dữ liệu hoặc thao tác tạo bảng vật lý |

Ví dụ làm sạch ở Silver: phát hiện thiếu mã nhân viên, giá trị ngày sai định dạng, dòng trùng hoặc mã đơn vị chưa đối chiếu được. Ví dụ ở Gold: tổng số nhân viên đang làm việc theo đơn vị và thời điểm. Không đưa việc tính chỉ số này thành điều kiện bắt buộc để lưu Silver.

## 2. Bức tranh hiện tại

```text
Người dùng đăng nhập
        |
        +-- Tải file từ máy --> API upload --> Kho tiếp nhận --+
        |                                                     |
        +-- Nguồn đã đăng ký --> API tạo job ------------------+
                                                              |
                                                   Hàng đợi PostgreSQL
                                                              |
                                                      Ingestion worker
                                                              |
                                                Đọc và kiểm tra đầu vào
                                                              |
                                                        Bronze / MinIO
                                                              |
                                           Data Admin chọn cấu hình xử lý
                                                              |
                                        Xem trước (tùy chọn, không lưu Silver)
                                                              |
                                          Gọi API làm sạch và chuẩn hóa
                                                              |
                                      Kiểm tra đủ điều kiện công bố Silver
                                               /              \
                                           Có lỗi           Đạt yêu cầu
                                             |                  |
                                     Báo cáo / cách ly    Silver Parquet
                                             |            + bản kê phiên bản
                                     Sửa nguồn, nạp lại          |
                                                        Gold / phân tích
                                                        CHƯA nối hoàn chỉnh
```

Những ranh giới cần nhớ:

1. Tải file được tiếp nhận trả `202` không có nghĩa file đã vào Bronze.
2. Ingestion job `SUCCESS` nghĩa là bước nạp Bronze thành công, không có nghĩa đã có Silver.
3. Bronze → Silver hiện phải gọi API riêng; không tự chạy khi ingestion thành công.
4. API chạy Silver hiện xử lý trong yêu cầu HTTP, chưa đưa vào hàng đợi worker.
5. Chưa có luồng công bố Gold và API chạy Gold hoàn chỉnh trong các router hiện tại.
6. API hỏi AI hiện dùng bộ trả lời mô phỏng và bộ thực thi trả dữ liệu rỗng.

## 3. Các tên đang dễ gây nhầm

| Tên trong API/code | Ý nghĩa | Nơi lưu chính | Ai thao tác |
| --- | --- | --- | --- |
| `source_system_id` | Hệ thống hoặc đơn vị nguồn, ví dụ hệ thống nhân sự | `app.source_system` | Quản trị cấu hình |
| `data_source_id` | Cách truy cập một nguồn cụ thể, ví dụ thư mục CSV, kết nối DB hoặc nguồn upload | `app.data_source` | Quản trị; backend tự tạo loại upload |
| `dataset_id` | Mã đăng ký bộ dữ liệu, ví dụ “Danh sách nhân viên”, có lĩnh vực, thông tin chủ quản nếu có và tầng dữ liệu | `app.dataset` | Quản trị đăng ký hoặc STAFF đặt tên qua upload; backend cấp mã |
| `ingestion_job_id` | Một lần nạp dữ liệu vào Bronze | `app.ingestion_job` | Backend cấp; UI dùng để theo dõi |
| `profile_id` | Tên cấu hình cách đọc cột và xử lý nguồn để đưa vào Silver | `app.silver_source_profile`; có hỗ trợ file cấu hình | Data Admin quản lý |
| `run_id` | Một lần chạy làm sạch và công bố Silver | `app.cleaning_run` | Backend cấp |
| `entity` | Tên bảng dữ liệu đích trong Silver | Cấu trúc đích và bản kê Silver | Data Admin cấu hình |
| `snapshot_id` | Mã một bộ phiên bản Silver được công bố | `app.silver_snapshot` và MinIO | Backend cấp |
| `scope` | Phạm vi các bảng Silver dùng chung phiên bản và kiểm tra liên kết | Cấu hình và metadata Silver | Data Admin cấu hình |

**`app.dataset` chỉ lưu thông tin đăng ký, không chứa 20.000 dòng nhân viên.** File gốc nằm trong MinIO Bronze; dữ liệu Silver được ghi thành Parquet trong MinIO. Bảng PostgreSQL quản lý thông tin, trạng thái và tham chiếu đến các file đó.

Một bộ dữ liệu có thể có nhiều lần nạp. Nạp lại cùng bộ dữ liệu tạo job mới; không cần tạo một dataset mới cho từng file. Tên file không quyết định quyền phòng ban.

## 4. Phân quyền thực tế hiện nay

| Nhóm | Tải file | Xem job, thống kê, lịch sử retry | Tạo dataset / nguồn | Chạy, xem và quản lý Silver |
| --- | --- | --- | --- | --- |
| `ADMIN`, `DATA_ADMIN` | Có | Toàn bộ phạm vi ingestion | Có | Có |
| `HR_STAFF` | Dataset có `domain=hr` | Các job thuộc `hr` | Tạo dataset qua upload trong `hr`; không tạo kết nối | Không |
| `ACADEMIC_STAFF` | Dataset có `domain=academic` | Các job thuộc `academic` | Tạo dataset qua upload trong `academic`; không tạo kết nối | Không |
| `ADMISSIONS_STAFF` | Dataset có `domain=admissions` | Các job thuộc `admissions` | Tạo dataset qua upload trong `admissions`; không tạo kết nối | Không |
| Ba role `*_MANAGER` khi không có thêm role khác | Không | Không | Không | Không |
| `VIEWER` | Không | Không | Không | Không |

Các ngoại lệ theo phân hệ:

- Danh sách và chi tiết `datasources` hiện cho `ADMIN`, `DATA_ADMIN`, `VIEWER` đọc; STAFF không có quyền này.
- Nhóm `/governance/*` hiện chỉ cho role **`DATA_ADMIN`**. Một tài khoản chỉ có `ADMIN` cũng không vượt qua kiểm tra này.
- Worker monitoring và retry thủ công chỉ dành cho `ADMIN`, `DATA_ADMIN`.
- Yêu cầu sửa dữ liệu có quyền riêng: quản trị tạo/giao/xác minh; người phụ trách thuộc phòng ban được xem và báo đã sửa theo điều kiện trong code.

**Giới hạn quan trọng:** upload và đọc ingestion hiện giới hạn theo `domain` lấy từ role, chưa đối chiếu từng `department_id` của người dùng. Nói “chỉ dữ liệu phòng ban mình” hiện chính xác ở mức ba lĩnh vực HR/Academic/Admissions, chưa phải mọi đơn vị con. Riêng phân hệ yêu cầu sửa dữ liệu đã kiểm tra cả phân công đơn vị đang hiệu lực. Hai cách kiểm tra này cần thống nhất.

## 5. Luồng A — tải file từ máy người dùng: hiện có

### 5.1. Điều kiện trước khi tải

Tầng Bronze phải được thiết lập. Người dùng có hai lựa chọn:

1. Gọi `GET /datasets` để chọn bộ đã có; UI hiển thị tên và tự gửi `dataset_id`.
2. Nếu chưa có bộ phù hợp, nhập `dataset_name` và tải file trong cùng `POST /ingestion/uploads`. Không gửi `dataset_id`. Backend tự tạo dataset và nguồn tiếp nhận theo lĩnh vực được cấp.

Với `HR_STAFF` một lĩnh vực, chỉ cần tên và file; lĩnh vực tự là `hr`. Quản trị hoặc tài khoản nhiều lĩnh vực phải chọn `domain` hợp lệ. Backend tự tạo/tái dùng hệ thống nguồn `UPLOAD_HR`, `UPLOAD_ACADEMIC` hoặc `UPLOAD_ADMISSIONS`. Nguồn mới chưa có `owner_department_id`; không tự suy đơn vị con hoặc tạo đơn vị giả từ role.

**Không bắt người dùng nhập ID.** Khi chọn bộ đã có, dataset phải đang hoạt động, thuộc Bronze và gắn hệ thống nguồn hoạt động. Việc quản trị tự đăng ký qua `POST /datasets` vẫn được hỗ trợ cho các nhu cầu khác; API quản lý source system tổng quát chưa có.

Ví dụ đăng ký dataset bằng tài khoản quản trị; `source_system_id` phải lấy từ hệ thống thật:

```http
POST /api/v1/datasets
Content-Type: application/json
Authorization: Bearer <access_token>
```

```json
{
  "dataset_code": "HR_EMPLOYEES",
  "dataset_name": "Danh sách nhân viên",
  "domain": "hr",
  "source_system_id": 12,
  "description": "Dữ liệu nhân viên do bộ phận nhân sự cung cấp"
}
```

Số `12` và các ID trong ví dụ tiếp theo chỉ minh họa, không phải ID được seed sẵn.

### 5.2. API tiếp nhận file

```http
POST /api/v1/ingestion/uploads
Authorization: Bearer <access_token>
Content-Type: multipart/form-data
```

| Trường | Bắt buộc | Cách dùng hiện tại |
| --- | --- | --- |
| `file` | Có | File `.csv`, `.xlsx`, `.xlsm`; không hỗ trợ `.xls`, PDF, JSON qua endpoint này |
| `dataset_id` | Khi chọn bộ cũ | UI tự gửi ID đã chọn; không gửi cùng thông tin tạo bộ mới |
| `dataset_name` | Khi tạo bộ mới | Tên dễ hiểu, tối đa 200 ký tự; không cần mã kỹ thuật |
| `description` | Không | Mô tả, chỉ dùng khi tạo bộ mới |
| `domain` | Khi tạo mới và có nhiều lựa chọn | `hr`, `academic`, `admissions`; một lĩnh vực STAFF thì tự lấy từ role |
| `parser_options` | Không | Chuỗi JSON hướng dẫn đọc file; mặc định `{}` |

Các lựa chọn cách đọc có hiệu lực: CSV dùng `delimiter`, `encoding`, `quote_char`, `skip_rows`; Excel dùng `sheet_name`, `header_row`, `skip_rows`. Khi không chọn sheet, worker chọn sheet đầu tiên. CSV upload hiện luôn có hàng tiêu đề. UI chỉ nên hiển thị các lựa chọn đúng với loại file.

Tên file hiện chỉ chấp nhận chữ không dấu, số, dấu gạch dưới, dấu gạch ngang và dấu chấm; bắt đầu bằng chữ/số/gạch dưới, tối đa 200 ký tự. Tên có dấu hoặc khoảng trắng sẽ bị từ chối với `UPLOAD_FILENAME_INVALID` dù nội dung hợp lệ.

Ví dụ STAFF tạo mới bằng curl, chạy ở nơi có file và đã có role `HR_STAFF`:

```bash
curl -X POST "$API_BASE/api/v1/ingestion/uploads" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F 'dataset_name=Danh sách nhân viên' \
  -F 'file=@/duong-dan/hr_employees_20000_valid.csv'
```

Không cần nhập mã nguồn, mã dataset hay lĩnh vực khi tài khoản chỉ có một lĩnh vực STAFF. Ví dụ nạp vào bộ đã có:

```bash
curl -X POST "$API_BASE/api/v1/ingestion/uploads" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -F 'dataset_id=27' \
  -F 'parser_options={"encoding":"utf-8","delimiter":","}' \
  -F 'file=@/duong-dan/hr_employees_20000_valid.csv'
```

`API_BASE` là địa chỉ backend, không bao gồm `/api/v1`. Với frontend dùng `FormData`, để trình duyệt tự tạo `Content-Type` và phần phân cách của multipart.

Backend thực hiện:

1. Kiểm tra quyền, hai lựa chọn bộ cũ/bộ mới, lĩnh vực khi tạo mới, loại file và tùy chọn đọc.
2. Đọc file theo từng phần, kiểm tra file không rỗng và không vượt dung lượng, tính mã kiểm tra nội dung SHA-256.
3. Nếu bộ cũ: kiểm tra dataset và lĩnh vực được phép. Nếu bộ mới: chuẩn bị nguồn quản lý theo lĩnh vực, kiểm tra tên trùng, tự cấp mã và tạo dataset Bronze. Sai quyền trả `403 UPLOAD_DOMAIN_FORBIDDEN`; trộn hai lựa chọn trả `422`; tên trùng trong nguồn upload cùng lĩnh vực trả `409`.
4. Tìm hoặc tự tạo `data_source` loại `UPLOAD` cho dataset và loại file. Người tải không phải đăng ký đường dẫn nguồn.
5. Lưu **nguyên file** vào bucket tiếp nhận riêng, ghi nhận phiên bản và đọc lại để xác minh.
6. Tạo job `UPLOAD_V1`, trạng thái `QUEUED`, lưu tham chiếu đến đúng phiên bản file; lưu chính thức dataset/nguồn mới và job cùng nhau.
7. Trả HTTP `202` và thông tin job để UI theo dõi. Lỗi tiếp nhận hoặc ghi job hủy các đăng ký vừa tạo; sau `202`, dataset vẫn được giữ nếu worker phát hiện file sai để người dùng nạp bản sửa vào cùng bộ.

Nếu thất bại trước khi tạo job thì không có job để polling; UI hiển thị lỗi của request. Kho tiếp nhận có thể đã nhận object nếu lỗi xảy ra sau bước ghi file; chưa có job không đồng nghĩa chắc chắn chưa ghi gì vào kho tiếp nhận.

### 5.3. Worker kiểm tra file rồi lưu Bronze

Worker lấy job từ PostgreSQL và thực hiện:

1. Kiểm tra lại nguồn, dataset, tầng Bronze và các ràng buộc đã lưu cùng job.
2. Đọc đúng `version_id` trong kho tiếp nhận; đối chiếu dung lượng và SHA-256.
3. Quét toàn bộ file để kiểm tra khả năng đọc và cấu trúc cơ bản.
4. Lưu báo cáo validation vào PostgreSQL. Không qua được kiểm tra thì không gọi bước ghi Bronze.
5. Nếu đạt, ghi nguyên bản CSV/Excel vào Bronze, kèm thông tin nguồn và lần nạp; đọc lại để xác minh.
6. Lưu thông tin đầu ra và chốt job `SUCCESS`.

Kiểm tra đầu vào upload hiện gồm: tiêu đề cột không rỗng/không trùng, số cột mỗi dòng hợp lệ, mã hóa và cấu trúc file đọc được, không vượt các giới hạn, không có dữ liệu thay đổi giữa các bước. Excel còn kiểm tra sheet, lỗi ô, công thức và kích thước giải nén. Công thức Excel bị từ chối; không chạy macro của file `.xlsm`. CSV có dòng trắng sai cấu trúc bị từ chối; dòng Excel trống hoàn toàn được ghi nhận là bỏ qua khi quét, còn file Bronze vẫn giữ nguyên.

**Upload validation không tự biết cột nào là mã nhân viên bắt buộc, không tự sửa dữ liệu, không kiểm tra mọi quy tắc nghiệp vụ và không tự suy ra cấu trúc Silver.** Việc này cần cấu hình xử lý ở bước sau.

Giới hạn mặc định trong code, có thể thay đổi bằng môi trường: file 50 MiB, 1.000.000 bản ghi, 500 cột, 1.000.000 ký tự mỗi ô, thời gian kiểm tra 60 giây, Excel giải nén tối đa 200 MiB. Đây không phải cam kết thời gian xử lý toàn bộ request hoặc giới hạn bộ nhớ toàn tiến trình.

### 5.4. UI theo dõi kết quả

Sau `202`, UI dùng `ingestion_job_id` từ response gọi `GET /ingestion/jobs/{job_id}` theo chu kỳ, ví dụ mỗi 3–5 giây khi màn hình theo dõi đang mở.

| `status` | Cách hiển thị |
| --- | --- |
| `QUEUED` | Đã tiếp nhận, đang chờ xử lý |
| `RUNNING` | Đang xử lý; có thể hiển thị `stage` |
| `SUCCESS` | Đã lưu Bronze, đang chờ xử lý Silver |
| `FAILED` | Lần nạp này thất bại; kiểm tra lỗi và lịch sử các lần chạy |
| `REJECTED` | Yêu cầu thu thập nguồn bị từ chối khi thiết lập job |
| `RETRY_WAIT` | Một job chạy lại đang chờ đến thời điểm được xử lý |

Job cũ vẫn là `FAILED` khi backend tạo một job retry mới. UI cần đọc `/attempts` để tìm lần tiếp theo; không chỉ chờ job cũ tự đổi sang `SUCCESS`. `stage` như `VALIDATING`, `WRITING`, `COMMITTING`, `RECONCILING` chỉ là bước xử lý, không phải một trạng thái thành công riêng.

## 6. Luồng B — thu thập từ nguồn đã đăng ký: hiện có

Đây là chức năng dành cho quản trị khi dữ liệu nằm trên server, bucket nguồn hoặc PostgreSQL. Không dùng nó thay upload từ laptop.

1. Chuẩn bị hệ thống nguồn và dataset Bronze có cùng `source_system_id`.
2. `POST /datasources/create`: đăng ký cách truy cập nguồn; `configuration` được kiểm tra theo loại nguồn.
3. `POST /ingestion/jobs`: yêu cầu worker đọc một file hoặc bảng cụ thể.
4. Worker lấy bản dữ liệu tại thời điểm thực thi, đọc và ghi Bronze; UI theo dõi job như luồng upload.

```json
{
  "data_source_id": 15,
  "dataset_id": 27,
  "source_table": "employees.csv"
}
```

Điểm khác upload:

- Endpoint không nhận nội dung file; vị trí và cách truy cập lấy từ nguồn đã đăng ký.
- Loại nguồn đang hỗ trợ là CSV, Excel, PostgreSQL. File phải ở nơi worker truy cập được; file trên Mac không tự xuất hiện trên Render.
- CSV/Excel được giữ nguyên bản; bảng PostgreSQL được chụp thành `snapshot.parquet` khi ghi Bronze.
- `validation_policy` là trường tùy chọn của request. Nếu có, backend ghim cấu hình kiểm tra và worker tạo báo cáo validation đầy đủ theo cấu hình đó. Nếu bỏ qua, luồng chỉ đọc/capture theo reader; **không được coi là đã chạy cùng bộ validation như upload**.
- Manual retry hiện chỉ hỗ trợ `UPLOAD_V1` có file đã ghim, chưa hỗ trợ nguồn thu thập thường. Auto retry đã có code nhưng chưa giới hạn chỉ nguồn đã ghim; cần rà soát trước khi coi retry nguồn ngoài là an toàn và lặp lại được.
- Bronze từ PostgreSQL chưa được API Bronze → Silver hiện tại tiếp nhận; API Silver đang chỉ nhận nguồn CSV/Excel.

## 7. Bronze → Silver: hiện có

### 7.1. Cấu hình xử lý là gì và ai tạo?

Data Admin tạo profile để nói rõ **cột nguồn có nghĩa gì và phải chuyển về dạng nào**. Upload không yêu cầu STAFF sửa `profiles.json`. Profile tạo qua API được lưu PostgreSQL; file `config/silver/profiles.json` chỉ là cách khai báo dự phòng đang được hỗ trợ, mặc định trống.

| Nhóm thông tin của profile | Nội dung |
| --- | --- |
| Gắn nguồn | `data_source_id`, `dataset_id`, `source_system_id`, `source_type`, `source_table`, `domain`, `entity` |
| Cấu trúc | `source_schema`, `source_schema_version`, `contracts` mô tả cấu trúc đích |
| Đối chiếu cột | `field_mapping`, `ignored_columns` |
| Cách đọc giá trị | `format_options`, `type_options`, `null_tokens` |
| Chuẩn hóa mã | `dictionaries`, `reference_namespaces`, `crosswalk_release`, `effective_at` |
| Chính sách và truy vết | `version`, `scope`, `collapse_exact_duplicates`, `source_preview_columns` |

Tạo: `POST /pipeline/profiles` với `profile_id` và `definition`. Sửa: `PUT /pipeline/profiles/{profile_id}` với `expected_revision` lấy từ lần GET gần nhất và toàn bộ `definition` mới. Trả `409` nếu người khác đã sửa trước.

Hiện profile gắn cả tên file `source_table`. Đổi tên file ở lần nạp sau có thể làm profile không khớp. Chưa có cơ chế tự nhận diện loại dữ liệu, tự gắn profile hay tự đoán mapping từ file. Bảng đối chiếu mã còn phải chuẩn bị bằng công cụ `scripts/standardization/register_crosswalk.py`, chưa có API quản lý tương ứng.

### 7.2. Xem trước

```http
POST /api/v1/pipeline/cleaning/ingestion-jobs/105/preview?limit=20
```

```json
{"profile_id":"hr-employees"}
```

API đọc và kiểm tra toàn bộ file Bronze, thực hiện làm sạch và chuẩn hóa, rồi trả tối đa 100 dòng mẫu cùng số liệu và lỗi. Không lưu cleaning run, hồ sơ cách ly hoặc Silver. `publication_checked=false` nghĩa là chưa kiểm tra đầy đủ liên kết với toàn bộ phiên bản Silver; preview đạt không bảo đảm công bố sẽ đạt.

### 7.3. Chạy thật

```http
POST /api/v1/pipeline/cleaning/ingestion-jobs/105
```

```json
{
  "profile_id": "hr-employees",
  "base_snapshot": "active"
}
```

Thứ tự thực hiện trong luồng nguồn thật:

1. Kiểm tra ingestion job đã `SUCCESS`, thuộc nguồn CSV/Excel, nguồn và dataset còn hoạt động, profile khớp các mã nguồn và tên file.
2. Đọc đúng phiên bản Bronze, kiểm tra nội dung, số dòng và cách đọc đã ghi nhận khi ingestion. Không lấy file từ máy người dùng lần nữa.
3. Kiểm tra thiếu dữ liệu theo cấu hình. Giá trị rỗng được nhận diện bằng danh sách đã khai báo; không tự bịa giá trị thay thế.
4. Kiểm tra định dạng và miền giá trị theo cấu trúc nguồn và các lựa chọn của profile.
5. Xử lý trùng: chỉ gộp dòng trùng hoàn toàn theo khóa khi cấu hình cho phép; dòng trùng có mâu thuẫn không tự chọn một bản bất kỳ.
6. Lưu số liệu cleaning, báo cáo và hồ sơ dòng bị cách ly nếu có.
7. Nếu có dòng bị cách ly: dừng công bố cả lô, trả `QUARANTINED`. Nếu không còn dòng để công bố: trả `EMPTY`.
8. Nếu cleaning đạt: đối chiếu tên cột, chuyển kiểu, chuẩn hóa giá trị/mã và đối chiếu danh tính theo phiên bản đã khai báo.
9. Kiểm tra cấu trúc Silver, trường bắt buộc, khóa, tính duy nhất, liên kết giữa các bảng và bằng chứng nguồn gốc. Lỗi ở bước này cũng có thể chặn công bố; không phải lỗi nào cũng đã có hồ sơ dòng cách ly ở bước cleaning.
10. Ghi dữ liệu Parquet và bản kê phiên bản vào MinIO; kiểm tra các object; ghi metadata phiên bản và cập nhật phiên bản hiện hành có kiểm soát thay đổi đồng thời.
11. Ghi nhận kết quả công bố và thông tin truy vết. Khi thành công trả `PUBLISHED` cùng `snapshot_id`.

Ví dụ chuẩn hóa chỉ thực hiện khi đã cấu hình: bỏ khoảng trắng đầu/cuối, thống nhất Unicode, đọc `10/11/2026` thành ngày, đọc dấu phân cách số đúng với nguồn, đổi `Có/Không` sang giá trị đúng/sai, đổi tên cột về tên thống nhất và đối chiếu mã phòng ban về mã dùng chung. Không mặc định rằng mọi profile đều bật tất cả các bước này.

**HTTP `200` của API này không đủ để kết luận thành công:** đọc `status` và `publication`. Một kết quả `QUARANTINED` hoặc `EMPTY` không tạo Silver mới. Lịch sử còn có hai trạng thái riêng là `cleaning_status` và `publication_status`, vì cleaning hoàn tất vẫn có thể bị chặn ở bước công bố.

### 7.4. Cách cập nhật và sử dụng phiên bản Silver

- Mỗi phiên bản là một bộ dữ liệu có bản kê, tham chiếu đến các file và thông tin nguồn.
- Chạy cập nhật một `entity` hiện thay **toàn bộ bảng đó** trong phiên bản mới, đồng thời giữ các bảng khác từ phiên bản nền. Chưa phải cơ chế thêm/cập nhật từng dòng theo khóa.
- Vì vậy, hiện phải nạp bản đầy đủ của bảng cần thay. Không dùng file “20 dòng phát sinh” để thay bảng 20.000 dòng nếu muốn giữ toàn bộ dữ liệu.
- `base_snapshot="active"` lấy phiên bản hiện hành của phạm vi. Mã snapshot cụ thể phải phù hợp với phiên bản hiện hành; `null` dành cho công bố đầu tiên khi chưa có phiên bản nền.
- Nếu có người khác công bố làm thay đổi phiên bản hiện hành, hệ thống chặn ghi đè. Cần lấy lại trạng thái mới và kiểm tra trước khi chạy lại.
- API `/activate` dùng để chọn lại một phiên bản đã công bố, không phải bước bắt buộc sau mỗi lần cleaning thành công. Nó xác minh dữ liệu trước khi đổi, dùng `expected_current_snapshot` để tránh ghi đè thay đổi của người khác và lưu lịch sử trong `app.silver_activation`.
- Xem dữ liệu qua `/data` đang đọc snapshot vào bộ nhớ rồi cắt trang; không phải cơ chế đọc Parquet lớn theo từng trang từ kho lưu trữ.

Các nhãn draft/approval và bộ 39 quy định nghiệp vụ trong tài liệu cũ không còn là cổng bắt buộc của luồng công bố này. Điều đó không bỏ kiểm tra cấu trúc, kiểu, khóa và nguồn gốc. Các phép tính phân tích cần được đặc tả ở Gold theo từng bài toán.

## 8. Khi dữ liệu có lỗi: hiện có và phần chưa nối kín

Luồng dự kiến sử dụng các API đã có:

1. Data Admin xem `/cleaning-runs/{run_id}/report` và `/quarantine-records`.
2. Tạo `/data-quality-issues`, gắn các hồ sơ dòng lỗi, đơn vị và người phụ trách.
3. Người phụ trách sửa file nguồn rồi gọi `/data-quality-issues/{issue_id}/uploads`; backend lấy dataset từ yêu cầu và giữ cách đọc đã ghi nhận nếu không chọn lại. Đợi Bronze thành công.
4. Người phụ trách gửi `/resolution` kèm job mới và ghi chú.
5. Data Admin chạy cleaning cho job mới rồi gửi `/verification` kèm cleaning run và kết quả đối chiếu.

`/resolution` đã nhận cả `UPLOAD_V1` và `EXECUTION_V1`. File upload phải do người phụ trách tải (đối chiếu job gốc nếu retry), đúng dataset/nguồn/tên file và thực sự là lần nạp mới hơn. Không lấy job thành công của file chấm công để xác nhận sửa file nhân viên dù dùng chung dataset. Yêu cầu sửa chỉ gộp hồ sơ cùng loại dữ liệu và file; lượt xác minh cũng phải đúng entity. Thiếu phân công đơn vị vẫn chặn nhận/nộp yêu cầu; `/staff/context` cho UI biết rõ điều kiện này.

Điều kiện quyền riêng của yêu cầu sửa: kiểm tra role còn hiệu lực và phân công đơn vị đang hiệu lực trong database. Chỉ người được giao và còn quyền mới được báo đã sửa; quản trị không được nộp thay chỉ nhờ có role quản trị. Xác minh đạt yêu cầu cleaning run đã công bố Silver và xác nhận đủ các hồ sơ liên quan. Trạng thái: `ASSIGNED` → `PENDING_VERIFICATION` → `VERIFIED`; xác minh không đạt quay về `ASSIGNED`.

Xem dòng nguồn cách ly qua `/quarantine-records/{record_id}/source` chỉ cho quản trị và chỉ trả các cột đã được cho phép tại thời điểm chạy, theo `source_preview_columns`. STAFF xem thông tin lỗi được gắn trong yêu cầu họ có quyền, không có quyền gọi trực tiếp toàn bộ API quarantine.

## 9. Hàng đợi, retry, đối soát và dọn file

Hàng đợi ingestion là PostgreSQL, **không dùng Redis queue**. Worker chạy riêng bằng `python -m app.ingestion_worker`. Có Redis trong Compose không có nghĩa ingestion sử dụng Redis.

| Chức năng | Hành vi hiện có | Giới hạn cần nhớ |
| --- | --- | --- |
| Nhận job | Worker nhận và giữ quyền xử lý có thời hạn | API chạy được nhưng worker dừng thì job vẫn chờ |
| Heartbeat worker | Ghi tín hiệu còn hoạt động, mặc định mỗi 10 giây; có file kiểm tra cho Docker | Khác heartbeat của job; `/health` của web API không thay thế kiểm tra worker |
| Heartbeat job | Gia hạn quyền xử lý mỗi 15 giây | Job hết hạn được xử lý phục hồi |
| Retry thủ công | Quản trị tạo job mới từ job upload `FAILED`, giữ `root_job_id`, tăng `attempt_number`, dùng lại phiên bản file và mã kiểm tra đã lưu | Không dùng để sửa nội dung sai; dữ liệu sai phải nạp bản đã sửa |
| Auto retry | Có phân loại lỗi, tạo job `RETRY_WAIT`; mặc định tối đa 3 lần tổng cộng, chờ 10 rồi 30 giây | Phân loại lỗi còn rộng; không phải mọi lỗi MinIO là lỗi tạm thời, một số lỗi trong validation bị gói lại nên không tự retry |
| Đối soát Bronze | Có `bronze_output`; lần chạy lại kiểm tra file đã tiếp nhận và Bronze cũ, giữ đúng số dòng và cách đọc | Silver đã đối chiếu nguồn gốc trong cùng chuỗi retry; vẫn chưa bao phủ trường hợp worker dừng trước khi lưu checkpoint |
| Dọn kho tiếp nhận | Khi worker rảnh, tìm chuỗi đã kết thúc quá hạn lưu giữ, mặc định 168 giờ, xóa đúng phiên bản staging | Chưa phải cơ chế hoàn chỉnh cho object mồ côi khi chưa tạo job; cần xử lý cạnh tranh với retry và ghi nhận đã dọn |

Không xóa Bronze chỉ vì file staging hết hạn. Không ghi đè trạng thái attempt cũ để giả làm một lần chạy mới. Khi cả chuỗi đã có lần thành công, giao diện nên trình bày kết quả chuỗi và lịch sử từng attempt riêng.

## 10. Danh sách toàn bộ API hiện đang đăng ký

Tất cả đường dẫn dưới đây dùng tiền tố **`/api/v1`**, trừ bảng vận hành. Đây là danh sách từ `app.openapi()` của code đã rà soát: **58 thao tác nghiệp vụ + 2 thao tác gốc/health = 60**, không tính trang tài liệu tự sinh. Không dùng số lượng endpoint để kết luận chức năng đã hoàn chỉnh.

Request có xác thực dùng `Authorization: Bearer <access_token>`. Các body dưới đây là JSON, trừ upload dùng multipart. Các ID phải lấy từ response hoặc danh sách tương ứng, không lấy số minh họa làm giá trị thật. Danh sách dùng `limit`, `offset` trừ nguồn dữ liệu dùng `page`, `page_size`.

### 10.1. Đăng nhập và phiên — 5 thao tác

| API | Cách dùng / đầu vào | Quyền |
| --- | --- | --- |
| `POST /auth/login` | `{username, password}` → access token, refresh token, thông tin người dùng | Chưa cần token |
| `POST /auth/refresh` | `{refresh_token}` → token mới; dùng khi access token hết hạn | Refresh token hợp lệ |
| `POST /auth/logout` | `{refresh_token}` → kết thúc phiên tương ứng | Xử lý theo refresh token được gửi |
| `GET /auth/sessions` | Danh sách phiên của chính người đang đăng nhập | Đã đăng nhập |
| `POST /auth/revoke-session/{session_id}` | Thu hồi một phiên thuộc chính tài khoản đó | Đã đăng nhập |

### 10.2. Hệ thống nguồn, dataset và kết nối — 8 thao tác

| API | Cách dùng / đầu vào | Quyền |
| --- | --- | --- |
| `GET /source-systems` | Chọn hệ thống nguồn đang hoạt động trước khi quản trị tạo dataset | ADMIN / DATA_ADMIN |
| `GET /datasets` | Danh sách dataset Bronze; tìm `search`, lọc `domain`, `limit`, `offset`; UI hiển thị tên | Quản trị hoặc STAFF đúng lĩnh vực |
| `GET /datasets/{dataset_id}` | Chi tiết bộ được phép upload; ngoài phạm vi trả `404` | Quản trị hoặc STAFF đúng lĩnh vực |
| `POST /datasets` | `{dataset_code, dataset_name, domain, source_system_id, description?}`; backend chọn tầng Bronze | ADMIN / DATA_ADMIN |
| `GET /datasources` | Xem nguồn; lọc `source_system_id`, `source_type`, `is_active`, `page`, `page_size` | ADMIN / DATA_ADMIN / VIEWER |
| `GET /datasources/{data_source_id}` | Xem chi tiết nguồn, không trả cấu hình bí mật | ADMIN / DATA_ADMIN / VIEWER |
| `POST /datasources/create` | `{source_system_id, source_name, source_type, connection_type, location, configuration, description?, is_active}`; dành cho nguồn có sẵn | ADMIN / DATA_ADMIN |
| `PATCH /datasources/{data_source_id}` | Sửa một phần thông tin nguồn bằng các trường của payload tạo | ADMIN / DATA_ADMIN |

### 10.3. Nạp và theo dõi Bronze — 9 thao tác

| API | Cách dùng / đầu vào | Quyền |
| --- | --- | --- |
| `POST /ingestion/uploads` | Multipart `{file, dataset_id, parser_options?}` cho bộ cũ hoặc `{file, dataset_name, description?, domain?, parser_options?}` cho bộ mới; nhận job `202` | Quản trị hoặc STAFF đúng lĩnh vực |
| `POST /ingestion/jobs` | `{data_source_id, dataset_id, source_table, validation_policy?}`; thu thập nguồn đăng ký sẵn | ADMIN / DATA_ADMIN |
| `GET /ingestion/jobs` | Lọc `data_source_id`, `dataset_id`, `status`, `mine_only`; phân trang | Quản trị / STAFF trong phạm vi |
| `GET /ingestion/jobs/{job_id}` | Theo dõi `status`, `stage`, số dòng, lỗi, `validation_report`, `output` | Quản trị / STAFF trong phạm vi |
| `GET /ingestion/jobs/stats` | Số job theo nhóm trạng thái trong phạm vi được đọc | Quản trị / STAFF trong phạm vi |
| `GET /ingestion/jobs/metrics` | Thời gian chờ, xử lý trung bình, tỷ lệ thất bại và job chờ lâu nhất | Quản trị / STAFF trong phạm vi |
| `GET /ingestion/jobs/{job_id}/attempts` | Các lần chạy thuộc cùng chuỗi `root_job_id` | Quản trị / STAFF trong phạm vi |
| `POST /ingestion/jobs/{job_id}/retry` | Không cần body; tạo job chạy lại từ file upload thất bại | ADMIN / DATA_ADMIN |
| `GET /ingestion/workers` | Danh sách worker, heartbeat, job đang chạy, trạng thái HEALTHY/OFFLINE | ADMIN / DATA_ADMIN |

### 10.4. Cấu hình và chạy Silver — 6 thao tác

Tất cả dành cho `ADMIN` / `DATA_ADMIN`.

| API | Cách dùng / đầu vào |
| --- | --- |
| `GET /pipeline/profiles` | Liệt kê profile với `limit`, `offset`; chưa có bộ lọc dataset riêng |
| `GET /pipeline/profiles/{profile_id}` | Lấy `definition`, `revision`, mã nội dung và nơi lưu |
| `POST /pipeline/profiles` | `{profile_id, definition}`; cấu trúc `definition` xem mục 7 và schema Swagger |
| `PUT /pipeline/profiles/{profile_id}` | `{expected_revision, definition}`; tránh ghi đè thay đổi đồng thời |
| `POST /pipeline/cleaning/ingestion-jobs/{ingestion_job_id}/preview` | `{profile_id}`, query `limit`; đọc toàn bộ, chỉ trả mẫu, không ghi Silver |
| `POST /pipeline/cleaning/ingestion-jobs/{ingestion_job_id}` | `{profile_id, base_snapshot?}`; chạy thật, mặc định nền `active` |

### 10.5. Phiên bản Silver — 5 thao tác

Tất cả dành cho `ADMIN` / `DATA_ADMIN`, **bắt buộc query `profile_id`** để xác định phạm vi/cấu trúc cần dùng.

| API | Cách dùng / đầu vào |
| --- | --- |
| `GET /pipeline/silver/snapshots` | Liệt kê phiên bản; `limit`, `offset` |
| `GET /pipeline/silver/snapshots/active` | Lấy phiên bản hiện hành |
| `GET /pipeline/silver/snapshots/{snapshot_id}` | Xem bản kê phiên bản và thông tin nguồn |
| `GET /pipeline/silver/snapshots/{snapshot_id}/data` | Query thêm `entity`, `limit`, `offset` để xem một bảng |
| `POST /pipeline/silver/snapshots/{snapshot_id}/activate` | Body `{expected_current_snapshot: "<mã hiện hành>"}` hoặc `null` khi chưa có; chọn lại phiên bản |

### 10.6. Lịch sử cleaning và dòng lỗi — 7 thao tác

Tất cả dành cho `ADMIN` / `DATA_ADMIN`.

| API | Cách dùng / đầu vào |
| --- | --- |
| `GET /cleaning-runs` | Lọc `dataset_id`, `ingestion_job_id`, `cleaning_status`, `publication_status`, `started_from`, `started_to`; phân trang |
| `GET /cleaning-runs/{run_id}` | Trạng thái, số dòng, snapshot và mã lỗi của một lượt |
| `GET /cleaning-runs/{run_id}/report` | Đọc báo cáo đã lưu của lượt đó |
| `GET /quarantine-records` | Lọc `cleaning_run_id`, `ingestion_job_id`, `dataset_id`, `owner_department_id`, `error_code`; phân trang |
| `GET /quarantine-records/summary` | Thống kê hồ sơ lỗi theo cùng bộ lọc, không phân trang |
| `GET /quarantine-records/{record_id}` | Chi tiết lỗi và vị trí dòng trong nguồn |
| `GET /quarantine-records/{record_id}/source` | Đọc giá trị dòng nguồn ở các cột được phép xem |

### 10.7. Giao sửa và xác minh — 10 thao tác

| API | Cách dùng / đầu vào | Quyền |
| --- | --- | --- |
| `POST /data-quality-issues` | `{title, description?, department_id, assignee_user_id?, quarantine_record_ids}` | ADMIN / DATA_ADMIN |
| `GET /data-quality-issues` | Lọc `department_id`, `assignee_user_id`, `status`, `mine_only`; phân trang | Quản trị; phòng ban theo phạm vi thực tế |
| `GET /data-quality-issues/stats` | Thống kê theo cùng quyền và bộ lọc của danh sách, không cắt trang | Quản trị; phòng ban theo phạm vi thực tế |
| `GET /data-quality-issues/departments` | Danh sách đơn vị còn hoạt động để chọn khi giao yêu cầu | ADMIN / DATA_ADMIN |
| `GET /data-quality-issues/assignees` | Query `department_id`, `domain`, `limit`, `offset`; chỉ người đủ role và phân công | ADMIN / DATA_ADMIN |
| `GET /data-quality-issues/{issue_id}` | Xem yêu cầu, hồ sơ lỗi liên quan và lịch sử xử lý | Người có quyền với yêu cầu |
| `PATCH /data-quality-issues/{issue_id}/assignment` | `{assignee_user_id}`; giao người phụ trách khi yêu cầu còn ASSIGNED | ADMIN / DATA_ADMIN |
| `POST /data-quality-issues/{issue_id}/uploads` | Multipart `file`, `parser_options?`; tự lấy dataset, giữ tên file; chưa đổi trạng thái yêu cầu | Đúng người được giao, còn quyền upload |
| `POST /data-quality-issues/{issue_id}/resolution` | `{ingestion_job_id, note}`; nhận EXECUTION_V1/UPLOAD_V1 đúng nguồn, file, người nộp và mới hơn | Đúng người được giao, còn quyền |
| `POST /data-quality-issues/{issue_id}/verification` | `{cleaning_run_id, verified, note, confirmed_record_ids?}`; khi xác nhận đạt phải đủ hồ sơ liên quan | ADMIN / DATA_ADMIN |

### 10.8. Catalog và nguồn gốc — 6 thao tác

Tất cả hiện chỉ dành cho **`DATA_ADMIN`**. Catalog này không thay thế đăng ký `app.dataset` khi upload.

| API | Cách dùng / đầu vào |
| --- | --- |
| `GET /governance/catalog` | Danh mục có hiệu lực; query `domain_id?` |
| `GET /governance/catalog/{domain_id}/{dataset}` | Chi tiết metadata theo lĩnh vực và tên bộ dữ liệu |
| `POST /governance/catalog` | Body `CatalogEntrySchema` theo Swagger; đăng ký metadata vào lĩnh vực đã đăng ký, không tạo bảng vật lý |
| `PATCH /governance/catalog/{domain_id}/{dataset}` | Body `CatalogUpdateSchema`; gửi `fields` sẽ thay danh sách cột |
| `DELETE /governance/catalog/{domain_id}/{dataset}` | Ẩn metadata, không xóa dữ liệu vật lý |
| `GET /governance/lineage` | Đồ thị nguồn gốc; query `domain_id`, `layer`, `search` tùy chọn |

Payload tạo catalog gồm `domain_id`, `dataset`, `layer`, `schema_version`, `description`, và mô tả `tables` hoặc dạng một bảng qua `fields`. Mỗi bảng gồm `name`, `description`, `columns`; mỗi cột gồm `name`, `data_type`, `nullable`, `description`. Khi mô tả nhiều bảng, dùng `tables` và không gửi danh sách `fields` khác với cấu trúc đó. PATCH cho phép đổi `layer`, `schema_version`, `description`, `tables`, `fields`; không đổi khóa `domain_id`/`dataset` trên URL.

Thông tin phiên bản Bronze, mã kiểm tra, profile và phiên bản đối chiếu mã cũng được lưu ở metadata/bản kê phục vụ truy vết. Không nên chỉ dựa vào đồ thị lineage để khẳng định mọi lần xử lý đã ghi đủ mọi cạnh: một số đoạn ghi lineage hiện cho phép bỏ qua lỗi để không làm hỏng kết quả xử lý chính.

### 10.9. Hỏi AI — 1 thao tác, chưa kết nối dữ liệu thật

| API | Cách dùng / đầu vào | Quyền hiện tại |
| --- | --- | --- |
| `POST /nlq/ask` | `{"question":"..."}` → `sql`, `rows` | Router chưa gắn kiểm tra đăng nhập/role |

Hiện dependency dùng `MockLLMClient` và `EmptyGoldQueryExecutor`; không được trình bày như kết quả truy vấn Gold thật. Cần bổ sung kiểm tra quyền khi hoàn thiện truy vấn dữ liệu thật.

Các module `users`, `dashboard`, `query-history` hiện chưa đăng ký endpoint nghiệp vụ. Chưa có API công bố Gold hoặc tự động biến dữ liệu Silver thành báo cáo hoàn chỉnh.

Luồng đích sau này là: Silver đã công bố → xây bảng/chỉ số Gold → cập nhật metadata phục vụ truy vấn → nhận câu hỏi → xác định dữ liệu liên quan → sinh và kiểm tra câu truy vấn chỉ đọc → thực thi theo quyền → trả kết quả, ghi lịch sử. Hiện service NLQ đã có các bước phân tích câu hỏi, lấy cấu trúc, sinh/kiểm tra SQL và gọi bộ thực thi, nhưng các kết nối thực tế ở hai đầu vẫn chưa hoàn thiện như trên.

### 10.10. Vận hành và tài liệu

| Đường dẫn không có `/api/v1` | Ý nghĩa |
| --- | --- |
| `GET /` | Thông báo ứng dụng |
| `GET /health` | Tiến trình API trả lời được; chưa kiểm tra PostgreSQL, MinIO hoặc worker |
| `/docs`, `/redoc` | Giao diện tài liệu tự sinh |
| `/openapi.json` | Hợp đồng API được tạo từ code đang triển khai |

### 10.11. Thông tin màn hình STAFF — 1 thao tác

| API | Cách dùng / đầu vào | Quyền |
| --- | --- | --- |
| `GET /staff/context` | Chính tài khoản: role, lĩnh vực, đơn vị hiệu lực, quyền thao tác và giới hạn upload; báo khi thiếu phân công | STAFF hoặc quản trị có role hiện hành |

## 11. Những chỗ chưa thống nhất cần xử lý trước

| Vấn đề | Hệ quả với người dùng | Hướng xử lý đề xuất |
| --- | --- | --- |
| Lần tải đầu trước đây bắt buộc dataset/nguồn có sẵn | Đã sửa: STAFF đặt tên bộ mới và tải file trong một yêu cầu | Backend tự quản lý mã và nguồn; vẫn cần tầng Bronze đã thiết lập |
| UI chưa có hợp đồng chọn dataset rõ ràng | Người dùng phải hiểu ID nội bộ | Chọn theo tên; ID chỉ dùng giữa frontend và backend |
| Phân quyền domain và đơn vị khác nhau giữa các module | Có thể upload được nhưng không nộp yêu cầu sửa được | Dùng chung quy định quyền và quan hệ chủ quản |
| Profile gắn tên file và cấu trúc kỹ thuật phức tạp | Đổi tên file hoặc lần tải đầu tiên cần can thiệp thủ công | Gắn cấu hình ổn định theo bộ dữ liệu/loại nội dung; quản trị xác nhận thay đổi cấu trúc |
| Quy trình sửa file upload | Đã khép kín: tải trong yêu cầu → Bronze → nộp → xác minh hoặc giao sửa tiếp | Kiểm tra nguồn, tên file, người nộp, lần nạp gốc và entity; vẫn yêu cầu phân công đơn vị thật |
| Validation nguồn đăng ký là tùy chọn | Hai cách vào Bronze có mức bằng chứng kiểm tra khác nhau | Quy định tối thiểu chung; nguồn thật phải có cấu hình kiểm tra phù hợp |
| Silver chạy trong HTTP và nạp dữ liệu vào bộ nhớ | Dễ timeout với dữ liệu lớn; mất kết nối không thể coi là đã hủy xử lý | Chuyển thành job riêng trước khi mở rộng quy mô |
| Retry/cleanup/đối soát chưa bao phủ đầy đủ các tình huống | Có thể lặp tác vụ hoặc còn staging mồ côi | Đã sửa giữ số dòng/cách đọc và đối chiếu tham chiếu Bronze → Silver sau phục hồi; tiếp tục rà soát cạnh tranh retry và dọn file |
| Gold, NLQ thật và giao diện báo cáo chưa hoàn chỉnh | Không thể dùng đầu ra mô phỏng làm báo cáo thật | Xây sau khi luồng Bronze → Silver và quyền truy cập được thống nhất |

## 12. Luồng sản phẩm đề xuất để thống nhất

Phần tạo bộ mới trong upload **đã triển khai** theo yêu cầu: API ở mục 10 hỗ trợ cả bộ mới và bộ cũ. Các phần còn lại như kiểm tra từng đơn vị con, tự gắn profile và job Silver vẫn là **đề xuất**. Dùng một luồng upload chính, không yêu cầu STAFF đi qua màn hình đăng ký nguồn/kết nối kỹ thuật.

### 12.1. Trải nghiệm tải file

```text
Đăng nhập
  → Mở “Nạp dữ liệu”
  → Chọn bộ dữ liệu đã có HOẶC đặt tên bộ dữ liệu mới
  → Chọn file và xác nhận cách đọc khi cần
  → Tải lên
  → Theo dõi kiểm tra và lưu Bronze
  → Data Admin xem trước, xác nhận cấu hình, chạy Silver
  → Silver sẵn sàng cho bước Gold
```

- Tài khoản một lĩnh vực: backend tự xác định phạm vi từ quyền; người dùng không nhập `domain` tùy ý.
- Tài khoản có nhiều phạm vi hoặc quản trị: UI cho chọn trong danh sách được cấp quyền; backend luôn kiểm tra lại. Chưa có API cấp danh sách phạm vi upload riêng, cần bổ sung hợp đồng này nếu sử dụng.
- Bộ dữ liệu đã có: UI hiển thị tên và tự gửi ID.
- Bộ dữ liệu mới: người dùng nhập tên dễ hiểu, có thể thêm mô tả. Backend tự cấp mã, gắn lĩnh vực và đơn vị hợp lệ, tạo/tái dùng nguồn upload được hệ thống quản lý. Không yêu cầu người dùng nhập `source_system_id`, `data_source_id`, đường dẫn MinIO hay tên bảng PostgreSQL.
- Kiểm tra từng đơn vị hợp lệ là phần đề xuất chưa triển khai: hiện backend kiểm tra lĩnh vực từ role, nguồn tạo mới chưa gán đơn vị; không suy đơn vị từ tên file hoặc giá trị tự khai.
- Không tạo dataset mới cho mọi lần nạp; nạp lại vào bộ đã chọn để giữ lịch sử.
- Tên trùng không đủ để tự kết luận là cùng dataset. Khi phát hiện trùng hoặc gửi lặp, trả lựa chọn/xung đột rõ ràng; cần có cơ chế chống tạo trùng khi request được gửi lại.

### 12.2. Hợp đồng upload đã cập nhật

Giữ **một** endpoint nạp file: `POST /ingestion/uploads`. Multipart đã hỗ trợ hai lựa chọn loại trừ nhau:

| Trường hợp | Người dùng cung cấp | Backend thực hiện |
| --- | --- | --- |
| Nạp vào bộ đã có | `file`, `dataset_id`, `parser_options?` | Kiểm tra quyền, tiếp nhận file, tạo job |
| Nạp bộ mới | `file`, `dataset_name`, `description?`, `parser_options?`; `domain` khi quản trị hoặc nhiều lĩnh vực | Xác định lĩnh vực theo quyền, tạo đăng ký bộ dữ liệu và nguồn upload, tiếp nhận file, tạo job |

Nếu đồng thời gửi thông tin chọn bộ cũ và tạo bộ mới thì trả `422 UPLOAD_DESTINATION_INVALID`. Mã ID do backend cấp trong response và UI lưu dùng cho lần sau. STAFF một lĩnh vực được bỏ trống `domain`; tài khoản nhiều lựa chọn phải chọn rõ. API trên Render chỉ có hành vi này sau khi code mới được triển khai.

`POST /datasets` và `/datasources/*` vẫn phục vụ quản trị, tích hợp nguồn đã có và quản lý danh mục. STAFF không cần gọi các API này để tải file lần đầu. Không thêm một endpoint upload thứ hai có cùng chức năng.

### 12.3. Xử lý Silver và phần nghiệp vụ

1. Sau Bronze, mặc định hiển thị “Chờ xử lý Silver”; không tự suy rằng dữ liệu đã sạch.
2. Data Admin cấu hình cách xử lý theo tên bộ dữ liệu và loại bảng; UI che các mã kỹ thuật. Profile có thể tái dùng khi cấu trúc không đổi.
3. Đổi tên file không nên làm mất liên kết cấu hình; thay đổi cột phải được phát hiện và yêu cầu xác nhận trước khi chạy.
4. Silver kiểm tra dữ liệu đủ để sạch, thống nhất và liên kết được. Không tự tính chỉ số Gold hay bịa giá trị thiếu.
5. Giữ chính sách hiện tại: có lỗi chặn thì chưa công bố lô mới, phiên bản Silver cũ vẫn dùng được. Người phụ trách sửa nguồn rồi nạp lại.
6. Giữ cách thay toàn bộ bảng cho giai đoạn đầu và ghi rõ trên UI; chỉ thêm nạp tăng dần khi có thiết kế riêng.
7. Khi cần chạy lâu, chuyển Silver sang job có trạng thái và lịch sử; chưa tự động chạy Silver theo mỗi upload khi chưa chốt cấu hình và chính sách này.

### 12.4. Những quyết định cần được xác nhận để coi là đã chốt

| Nội dung | Phương án đề xuất |
| --- | --- |
| Lần tải đầu của STAFF | Đã triển khai tạo bộ mới trong upload, giới hạn theo lĩnh vực của role |
| Mã nội bộ | Backend cấp; UI chọn theo tên, không yêu cầu gõ ID |
| Nguồn upload | Hệ thống quản lý; STAFF không đăng ký kết nối hoặc source system |
| Quyền dữ liệu | Kiểm tra lĩnh vực và đơn vị thực tế thống nhất ở upload, xem job và sửa lỗi |
| Mức kiểm tra Bronze | Kiểm tra khả năng đọc, cấu trúc tối thiểu, giới hạn và tính toàn vẹn; không sửa bản gốc |
| Kích hoạt Silver | Data Admin kiểm soát; chưa tự động công bố sau upload |
| Cập nhật bảng Silver | Bản đầy đủ thay toàn bộ bảng; giữ lịch sử phiên bản |
| Lô có lỗi chặn | Không công bố lô mới; trả báo cáo và thực hiện quy trình sửa nguồn |
| Gold và AI | Xây trên Silver đã công bố; công thức nghiệp vụ thuộc từng mô hình Gold |

Chỉ các phần đánh dấu đã triển khai mới là hành vi hiện có trong code. Các thay đổi còn lại cần tiếp tục thống nhất và cập nhật code, kiểm thử, OpenAPI và tài liệu theo cùng hợp đồng; tránh thêm từng ngoại lệ theo mỗi lần thử Swagger.

## 13. Điều kiện triển khai và kiểm tra nghiệm thu

Web API, PostgreSQL, kho MinIO truy cập được và ingestion worker là các thành phần tách biệt. `render.yaml` hiện chỉ khai báo web service và database; không tự cung cấp worker hoặc MinIO. Đưa API lên Swagger không tự hoàn thiện hạ tầng xử lý.

Database đích phải có schema, quyền, dữ liệu nền cần thiết và các SQL migration theo thứ tự, gồm ingestion, retry, cleaning/quarantine, đối chiếu mã, Silver và `20261110_silver_management.sql`. Migration trên local không cập nhật Render. Nhánh code và bản triển khai thực tế quyết định API hiện trên `/openapi.json`; restart bản cũ không bổ sung API mới.

Kiểm tra nghiệm thu cho luồng upload thống nhất, sau khi triển khai thay đổi:

- STAFF chưa có dataset vẫn tải được bộ mới bằng tên, không phải nhập ID hoặc đăng ký nguồn kỹ thuật.
- Lần nạp tiếp theo chọn cùng bộ dữ liệu, có job mới và lịch sử riêng.
- Tải chéo phạm vi bị từ chối; không tin lĩnh vực/đơn vị do client tự khai.
- File hỏng bị chặn, có trạng thái và lỗi rõ; chưa lưu Bronze thành công.
- Job `SUCCESS` có thể đọc lại đúng bản Bronze bằng phiên bản và mã kiểm tra đã lưu.
- Worker tắt thì UI thể hiện đang chờ, không báo giả đã hoàn tất.
- Data Admin chạy được preview và Silver với cấu hình đúng; đổi tên file cùng cấu trúc không làm mất cấu hình theo thiết kế mới.
- Lô có lỗi chặn không thay phiên bản Silver hiện hành; lô đạt tạo phiên bản truy vết được về Bronze.
- Nạp file đã sửa khép kín được yêu cầu `/resolution` và `/verification`.
- Retry không làm mất lịch sử hoặc công bố trùng; sau tái dùng Bronze vẫn chạy tiếp Silver được.
- Dọn staging không xóa phiên bản còn cần cho retry và không ảnh hưởng Bronze đã công bố.

## 14. Bằng chứng và tài liệu chi tiết

Các đường dẫn dưới đây tương đối với tài liệu trong repository, dùng để đối chiếu và cập nhật khi code thay đổi:

- [Đăng ký các router](../app/main.py), [mã role](../app/core/roles.py).
- [API dataset và hệ thống nguồn](../app/modules/datasources/datasets_router.py), [kiểm tra đích upload](../app/repositories/upload_repo.py).
- [API ingestion](../app/modules/ingestion/router.py), [tiếp nhận upload](../app/modules/ingestion/services/uploads.py), [kiểm tra file upload](../app/modules/ingestion/services/upload_capture.py).
- [Kiểm tra đầu vào](../app/modules/ingestion/services/input_validator.py), [capture và ghi Bronze](../app/bronze_capture.py).
- [Worker](../app/ingestion_worker.py), [thực thi ingestion](../app/modules/ingestion/services/execution.py), [hàng đợi và retry](../app/repositories/ingestion_repo.py).
- [Chạy Bronze → Silver](../app/modules/pipeline/source_cleaning.py), [cấu trúc profile](../app/modules/pipeline/source_profiles.py), [công bố Silver](../app/modules/pipeline/silver_publication.py).
- [API yêu cầu sửa và xác minh](../app/modules/pipeline/data_quality_issues_router.py), [xử lý yêu cầu sửa](../app/modules/staff/services/quality_issues.py), [Catalog và lineage](../app/modules/governance/router.py), [dependency NLQ thực tế](../app/providers.py).
- [Tải file](ingestion-uploads.md), [vận hành ingestion](ingestion-reliability.md), [quản lý Silver](silver-management-api.md), [cấu hình nguồn Silver](standardization/source-silver-flow.md), [triển khai Render](render.md).

Khi có khác biệt giữa mô tả cũ và mục “hiện có” trong tài liệu này, đối chiếu code và cập nhật tài liệu. Khi có khác biệt giữa “hiện có” và “đề xuất”, chưa tự hiểu phần đề xuất là chức năng đã chạy.
