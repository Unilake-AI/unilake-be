# API dành cho nhân viên phòng ban

Ngày cập nhật: **10/11/2026**. Tiền tố API: `/api/v1`.

Phạm vi của STAFF là cung cấp dữ liệu, theo dõi lần nạp, nhận yêu cầu sửa và nộp bản sửa để quản trị xác minh. Chạy Cleaning, cấu hình hoặc công bố Silver, retry thủ công và quản lý worker vẫn dành cho quản trị. Các thao tác chưa được cấp không được mở thêm chỉ vì dùng giao diện STAFF.

## 1. Phạm vi ba role

| Role | Lĩnh vực được nạp và xem ingestion |
| --- | --- |
| `HR_STAFF` | `hr` — Nhân sự |
| `ACADEMIC_STAFF` | `academic` — Đào tạo |
| `ADMISSIONS_STAFF` | `admissions` — Tuyển sinh |

Nạp và đọc ingestion hiện được giới hạn theo lĩnh vực, không chỉ các file cá nhân. Yêu cầu sửa dữ liệu còn yêu cầu đúng đơn vị đang công tác; chỉ người được giao mới được tải bản sửa và báo đã sửa. Người cùng đơn vị có thể xem yêu cầu theo phạm vi, nhưng không được nộp thay. Truy cập chi tiết ngoài phạm vi trả `404`.

Quyền nhận/sửa yêu cầu được đối chiếu với role hiện hành trong database và role trong token, cùng hồ sơ nhân sự và phân công còn hiệu lực. Không tự tạo đơn vị hoặc phân công giả từ tên role.

## 2. Khi mở giao diện

Sau đăng nhập, gọi:

```http
GET /api/v1/staff/context
Authorization: Bearer <access_token>
```

Response gồm:

- `user_id`, `username`, `roles`: thông tin chính tài khoản hiện tại.
- `domains`: các lựa chọn gồm mã lĩnh vực và tên tiếng Việt; `default_domain` khi chỉ có một lĩnh vực.
- `departments`: các đơn vị có phân công đang hiệu lực, kèm mã và tên để UI hiển thị.
- `can_upload`, `can_resolve_issues`, `can_manage_silver`: quyền phục vụ hiển thị chức năng. Backend vẫn kiểm tra lại ở từng API.
- `needs_department_assignment`, `note`: cho biết cần hoàn thiện phân công đơn vị trước khi nhận/nộp yêu cầu sửa.
- `accepted_extensions`, `max_file_bytes`, `max_records`: loại file và giới hạn tiếp nhận đang cấu hình.

`can_resolve_issues=true` không có nghĩa được nộp mọi yêu cầu trong đơn vị: vẫn phải là người đang được giao. STAFF chưa có phân công đơn vị vẫn nạp dữ liệu trong lĩnh vực được cấp, nhưng chưa thể nhận hoặc nộp yêu cầu sửa. UI cần hiển thị lý do này thay vì cho rằng chưa có dữ liệu.

Các role chỉ có `VIEWER` hoặc `*_MANAGER` không được sử dụng màn hình upload STAFF qua endpoint này. Quyền của MANAGER trong phân hệ yêu cầu sửa trước đó vẫn được giữ, không tự bổ sung quyền upload cho MANAGER.

## 3. Chọn hoặc tạo bộ dữ liệu và nạp file

| API | Mục đích |
| --- | --- |
| `GET /datasets?search=...&domain=...&limit=20&offset=0` | Tìm bộ dữ liệu theo tên trong lĩnh vực được phép |
| `GET /datasets/{dataset_id}` | Xem thông tin bộ được phép nạp; mã ngoài phạm vi trả `404` |
| `POST /ingestion/uploads` | Nạp file vào bộ cũ hoặc tạo bộ mới trong cùng yêu cầu |

Tạo mới dùng multipart:

```text
file = file người dùng chọn
dataset_name = Danh sách nhân viên
description = mô tả tùy chọn
```

STAFF có một lĩnh vực không cần gửi `domain`; nhiều lĩnh vực cần chọn một mục từ `domains`. Không gửi `dataset_id` khi tạo mới.

Nạp tiếp vào bộ đã có: UI cho chọn theo tên và tự gửi `dataset_id`; không gửi `dataset_name`, `description` hoặc `domain`. Không bắt người dùng gõ mã số. Bộ dữ liệu trùng tên không tự được tạo lại ở mỗi lần nạp. Số dòng thay đổi không phải lý do để tạo dataset mới.

Upload trả `202` và một job `QUEUED`. Chưa chuyển sang Silver ở bước này. Xem [hướng dẫn upload](ingestion-uploads.md) về cách đọc file và lỗi tiếp nhận.

## 4. Theo dõi và tra cứu ingestion

| API | Cách dùng |
| --- | --- |
| `GET /ingestion/jobs` | Lọc theo `dataset_id`, `data_source_id`, `status`, `mine_only`, `limit`, `offset` |
| `GET /ingestion/jobs/{job_id}` | Xem trạng thái, số dòng, lỗi và `validation_report`; có `quality_issue_id` nếu tải từ yêu cầu sửa |
| `GET /ingestion/jobs/{job_id}/attempts` | Các lần chạy cùng chuỗi; job cũ không bị đổi trạng thái thành công khi một lần khác đã thành công |
| `GET /ingestion/jobs/stats` | Tổng số job, queued, running, success, failed và `retry_wait` riêng |
| `GET /ingestion/jobs/metrics` | Thời gian chờ/xử lý và tỷ lệ lỗi trong phạm vi lĩnh vực |

`status` nhận `QUEUED`, `RETRY_WAIT`, `RUNNING`, `SUCCESS`, `FAILED`, `REJECTED`. Lọc `mine_only=true` lấy cả các attempt do hệ thống chạy lại từ file của chính người dùng, không lấy ID người dùng tùy ý từ client.

UI có thể polling chi tiết mỗi 3–5 giây khi người dùng đang mở màn hình. Sau `SUCCESS`, hiển thị “Đã lưu Bronze — chờ xử lý Silver”. Nếu `FAILED`, đọc lỗi và `/attempts`; lỗi file cần sửa nguồn rồi nạp bản mới. STAFF không được gọi retry thủ công để chạy lại lỗi hạ tầng.

## 5. Nhận yêu cầu sửa

```http
GET /api/v1/data-quality-issues?mine_only=true&status=ASSIGNED
GET /api/v1/data-quality-issues/stats?mine_only=true
GET /api/v1/data-quality-issues/{issue_id}
```

Danh sách và thống kê cùng áp dụng phạm vi đơn vị/lĩnh vực, cùng các bộ lọc `department_id`, `assignee_user_id`, `status`, `mine_only`. `limit`, `offset` chỉ cắt danh sách, không cắt tổng thống kê.

Chi tiết yêu cầu có lý do và vị trí dòng lỗi, `entity`, `source_table`, phiên bản Bronze và lịch sử. STAFF xem các thông tin được gắn vào yêu cầu có quyền, không được mở toàn bộ kho quarantine hoặc tự sửa bản Bronze.

## 6. Tải file đã sửa trong yêu cầu

```http
POST /api/v1/data-quality-issues/{issue_id}/uploads
Content-Type: multipart/form-data
```

Chỉ cần gửi `file`. Giữ đúng tên file nguồn hiển thị trong yêu cầu. Backend lấy dataset từ yêu cầu đã giao; không cần người dùng nhập `dataset_id`, nguồn hay lĩnh vực.

`parser_options` là tùy chọn: bỏ trống để giữ cách đọc đã ghi nhận của bản nguồn, gồm dấu phân cách CSV, mã hóa hoặc sheet Excel. Chỉ gửi JSON riêng khi thực sự đổi cách đọc. Nếu bản nguồn quá cũ chưa lưu cách đọc, API trả `RESOLUTION_PARSER_REQUIRED` để yêu cầu chọn rõ; không tự đoán.

Upload yêu cầu có hàng tiêu đề. Nguồn cũ khai báo không có hàng tiêu đề cần quản trị thống nhất lại trước khi sửa qua upload; API không âm thầm đổi cách đọc.

Điều kiện:

- Là người hiện được giao, còn role và phân công đơn vị phù hợp.
- Yêu cầu đang `ASSIGNED`; đang chờ xác minh hoặc đã xác minh trả `409`.
- Yêu cầu xác định một file và một loại dữ liệu. Không trộn lỗi nhân viên và chấm công vào cùng yêu cầu dù chung dataset HR.
- File qua cùng luồng tiếp nhận, validation và worker như upload bình thường.

Response `202` có `ingestion_job_id`, `dataset_id`, `quality_issue_id`. Tải xong chưa có nghĩa đã sửa thành công và chưa đổi trạng thái yêu cầu. UI tự giữ ID job trong response và theo dõi đến `SUCCESS`.

## 7. Báo đã sửa

Sau job thành công, gọi:

```http
POST /api/v1/data-quality-issues/{issue_id}/resolution
```

```json
{
  "ingestion_job_id": 125,
  "note": "Đã bổ sung họ tên ở các dòng được yêu cầu"
}
```

`125` chỉ minh họa; UI tự dùng ID từ response upload, không yêu cầu người dùng nhập tay.

Backend kiểm tra:

1. Đúng người được giao, đúng phạm vi và yêu cầu còn `ASSIGNED`.
2. Job đã lưu Bronze thành công, đúng dataset, nguồn và tên file cần sửa; không dùng file chấm công để báo đã sửa file nhân viên.
3. Với upload, file do chính người phụ trách tải. Nếu hệ thống retry, đối chiếu người tải từ job gốc.
4. Nếu file được tải trong một yêu cầu cụ thể, không dùng nó để nộp sang yêu cầu khác.
5. Lần nạp gốc mới hơn bản có lỗi và lần nộp trước. Một attempt mới của file cũ không được coi là bản sửa mới.

Có thể dùng file tải qua `/ingestion/uploads` bình thường nếu đáp ứng đầy đủ các điều kiện trên; không bắt buộc tải lại chỉ để lấy nhãn yêu cầu. Các job `EXECUTION_V1` vẫn được hỗ trợ theo luồng thu thập nguồn cũ.

Đạt điều kiện thì chuyển `PENDING_VERIFICATION`, lưu job sửa và ghi chú vào lịch sử trong cùng giao dịch. Job đang chờ, thất bại hoặc sai bằng chứng trả `422`; không đổi trạng thái yêu cầu.

## 8. Data Admin giao và xác minh

Các API hỗ trợ chọn theo tên thay vì nhập mã kỹ thuật:

```http
GET /api/v1/data-quality-issues/departments
GET /api/v1/data-quality-issues/assignees?department_id=7&domain=hr&limit=20&offset=0
```

Chỉ quản trị được gọi. Danh sách người phụ trách chỉ gồm tài khoản còn hoạt động, đúng role lĩnh vực và phân công đang hiệu lực trong đơn vị đã chọn. Các ID trong URL là giá trị UI lấy từ lựa chọn, không phải số được seed cố định.

Quản trị tạo `/data-quality-issues` từ các hồ sơ cùng dataset, loại dữ liệu, nguồn và file; giao người phụ trách qua `assignee_user_id` hoặc `/assignment`. Không giao người chưa có phân công hợp lệ.

Sau khi STAFF nộp, Data Admin chạy cleaning cho job sửa rồi gọi `/verification`. Lượt kiểm tra phải đúng job, dataset **và entity** của yêu cầu. Xác nhận đạt cần đã công bố Silver và xác nhận đủ các hồ sơ liên quan; việc đối chiếu từng dòng vẫn là trách nhiệm của người xác minh.

```text
ASSIGNED
  → STAFF tải file sửa và chờ Bronze thành công
  → STAFF gửi resolution
  → PENDING_VERIFICATION
  → Data Admin kiểm tra và xác minh
      → VERIFIED nếu đạt
      → ASSIGNED nếu chưa đạt, kèm lý do trong lịch sử
```

Sau khi bị từ chối, STAFF phải nạp một bản mới rồi nộp tiếp; không gửi lại job của lần trước. Người khác không được nộp thay, kể cả quản trị không phải người phụ trách.

## 9. Điều kiện cấu hình và triển khai

Không cần migration mới cho các API này. Tham chiếu yêu cầu sửa được lưu trong `upload_input` đã có. Database phải có các migration upload, reliability, cleaning runs, quarantine, pipeline lineage, `20261106_data_quality_issues.sql` và `20261106_issue_assignees.sql`.

Tài khoản STAFF muốn nhận và xử lý yêu cầu cần:

- Role STAFF tương ứng trong `app.user_role`.
- `app.app_user.employee_id` liên kết hồ sơ nhân sự có `employee_status=ACTIVE`.
- Một phân công `app.assignment` có `assignment_status=ACTIVE`, nằm trong khoảng ngày hiệu lực, thuộc đơn vị đang `ACTIVE`.

Tài khoản seed có role nhưng chưa có hồ sơ/phân công sẽ thấy `needs_department_assignment=true`. Đây là thông tin quản trị cần thiết, không được thay bằng phân công mẫu tự tạo. Không đưa dữ liệu nhân viên upload vào bảng nhân sự quản lý tài khoản chỉ để vượt kiểm tra này.

API và worker cần PostgreSQL/MinIO và tài khoản kho tiếp nhận/Bronze được cấu hình. Web API trên Render chỉ cập nhật khi code được triển khai lên nhánh service đang theo dõi; thay đổi local không tự cập nhật Render.

## 10. Kiểm thử

```bash
python -m pytest -q
STAFF_API_INTEGRATION_TEST=1 python -m pytest -q tests/test_staff_workflow_postgres.py
```

Lệnh thứ hai dùng cấu hình PostgreSQL của môi trường chạy. Nó tạo schema có tên ngẫu nhiên riêng cho kiểm thử, hoàn tác dữ liệu giữa các bài và chỉ xóa schema đó khi xong. Không dùng schema `app` để lưu dữ liệu kiểm thử. MinIO được thay bằng kho trong bộ nhớ; worker và các thao tác PostgreSQL được chạy thật.

CI bật `STAFF_API_INTEGRATION_TEST=1` để kiểm tra luồng upload → worker → gửi bản sửa → xác minh cùng các trường hợp vượt quyền, sai file/loại dữ liệu, sai chủ file, file lỗi, thiếu phân công, role bị thu hồi và vòng sửa lại sau khi bị từ chối.

## 11. Vị trí xử lý trong code

Toàn bộ phần điều phối chức năng STAFF nằm trong `app/modules/staff/services/`:

| File | Trách nhiệm |
| --- | --- |
| `context.py` | Lấy quyền, lĩnh vực, phòng ban và các lựa chọn cho giao diện |
| `access.py` | Xác định lĩnh vực được upload và xem lần nạp |
| `datasets.py` | Tìm và xem bộ dữ liệu trong phạm vi được cấp |
| `uploads.py` | Kiểm tra thông tin tải file, chọn hoặc tạo bộ dữ liệu, lưu file tiếp nhận và tạo lần nạp |
| `ingestion.py` | Tra cứu lần nạp, thống kê, số liệu theo dõi và lịch sử chạy lại |
| `quality_issues.py` | Xem yêu cầu, tải bản sửa, báo đã sửa; kiểm tra quyền và trạng thái khi quản trị giao hoặc xác minh |

Các router hiện có giữ nguyên đường dẫn, tham số và mẫu trả về. Router nhận yêu cầu, xác thực tài khoản rồi gọi service; không giữ thêm một bản quy tắc nghiệp vụ. Các module khác gọi qua `app/modules/staff/contracts.py`; mẫu dữ liệu STAFF nằm trong `app/modules/staff/schemas.py`.

Truy vấn và lưu database tiếp tục nằm trong `app/repositories/`, gồm `department_access_repo.py`, `staff_dataset_repo.py`, `upload_repo.py`, `ingestion_repo.py` và `quality_issue_repo.py`. Service dùng chung phiên làm việc của yêu cầu để giữ việc đổi trạng thái và ghi lịch sử trong cùng giao dịch.

Worker vẫn xử lý validation và lưu Bronze qua module ingestion. Xác thực, phiên đăng nhập và xử lý Silver tiếp tục dùng các module tương ứng. Việc gom service không tạo endpoint, thay đổi quyền hoặc yêu cầu migration mới.
