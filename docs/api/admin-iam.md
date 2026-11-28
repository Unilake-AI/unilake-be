# Quản trị tài khoản và phân quyền

`ADMIN` quản lý danh tính, phân công và quyền truy cập. Tài khoản cần vận hành nguồn dữ liệu, dataset hoặc pipeline phải có thêm `DATA_ADMIN`. Giao diện cho phép chuyển giữa hai không gian làm việc khi tài khoản có cả hai vai trò.

## Tài khoản và nhật ký

Các API dưới đây dùng tiền tố `/api/v1` và yêu cầu `ADMIN`.

| API | Chức năng |
| --- | --- |
| `GET /users` | Danh sách tài khoản và vai trò. Hỗ trợ `search` theo tên đăng nhập, `limit`, `offset`. |
| `POST /users` | Tạo tài khoản từ `username`, `password` (12–256 ký tự) và mảng `roles`. Trả về `201` với `user_id`, `409` nếu trùng tên đăng nhập hoặc `422` nếu dữ liệu không hợp lệ. Lỗi validation chỉ trả vị trí và loại lỗi cùng thông báo chung. |
| `PATCH /users/{user_id}/access` | Gán vai trò hoặc khóa, mở khóa tài khoản. Gửi `roles`, `is_active`, `expected_roles` và `expected_is_active`. Trả về `204`, hoặc `409` nếu tài khoản đã thay đổi hoặc thao tác làm mất quyền quản trị của chính người thực hiện. |
| `GET /users/organization` | Danh sách phòng ban hoạt động và chức vụ. |
| `PATCH /users/{user_id}/employee` | Liên kết tài khoản với hồ sơ nhân viên. |
| `POST /users/assignments` | Tạo phân công cho nhân viên. |
| `GET /users/summary` | Số tài khoản tổng, đang hoạt động và số tài khoản hoạt động có phân công hiện hành theo phòng ban. Một tài khoản có thể xuất hiện ở nhiều phòng. |
| `GET /users/audit` | Nhật ký đăng nhập thành công, tạo tài khoản, đổi vai trò, khóa/mở khóa, liên kết hồ sơ và thêm phân công. Mỗi sự kiện có người thực hiện, đối tượng nếu đã xác định, thời điểm và `outcome`. Thao tác thành công lưu `changes` theo danh sách trường cho phép. Từ chối nghiệp vụ sau xác thực lưu mã lỗi. Hỗ trợ `limit` và `offset`. |

Các cập nhật quyền được tuần tự hóa và kiểm tra lại quyền ADMIN của người thao tác. Hệ thống chặn tự khóa và tự gỡ vai trò ADMIN để luôn còn tài khoản quản trị đang hoạt động. Khóa tài khoản vô hiệu hóa access token và refresh token thuộc các phiên đang hoạt động. Sau khi mở khóa, người dùng đăng nhập lại. Khi đổi vai trò, backend đối chiếu JWT với vai trò hiện tại và yêu cầu làm mới nếu có thay đổi.

## Domain Owner

| API | Vai trò | Chức năng |
| --- | --- | --- |
| `GET /pipeline/profiles/owners/grants` | `ADMIN`, `DATA_ADMIN` | Xem các grant chưa bị thu hồi, gồm mã grant, domain và mã tài khoản. |
| `POST /pipeline/profiles/owners/grants` | `ADMIN` | Cấp grant cho trưởng phòng đủ điều kiện bằng `domain`, `user_id`, `reason`. |
| `GET /pipeline/profiles/owners/grants/audit` | `ADMIN` | Xem lịch sử cấp, thu hồi thành công, gồm người thực hiện, đối tượng, lĩnh vực, mã grant và thời điểm. Hỗ trợ `limit` và `offset`. |
| `POST /pipeline/profiles/owners/grants/{grant_id}/revoke` | `ADMIN` | Thu hồi grant và lưu `reason`. |

Cấp grant yêu cầu tài khoản và nhân viên đang hoạt động, đúng vai trò Manager và có phân công hiện hành tại phòng ban của lĩnh vực. Phê duyệt và công bố kiểm tra lại các điều kiện tương ứng. Nội dung lý do cấp hoặc thu hồi được giữ trong hồ sơ grant. Lịch sử vận hành và lineage nằm trong phân hệ dữ liệu.

## Migration

Chạy chuỗi SQL qua `make db-upgrade` trước khi phát hành ứng dụng lên cơ sở dữ liệu hiện có. Migration giới hạn danh mục quyền `ADMIN` về IAM/hệ thống và nâng cấp bảng `app.iam_event` để lưu kết quả và thay đổi. Các bản ghi cũ giữ trạng thái thành công và có thể thiếu chi tiết thay đổi. Việc gán vai trò cho tài khoản được quản lý qua API tài khoản.
