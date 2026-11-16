# Thông báo và yêu cầu xử lý lỗi

Thông báo lấy trực tiếp từ lịch sử `app.data_quality_issue_event`. Khi quản trị tạo/giao yêu cầu, duyệt trả file, nhân viên nộp bản sửa hoặc quản trị xác minh/từ chối, sự kiện mới tự xuất hiện trong feed. Không cần gửi thêm thông báo thủ công và không nhân bản dữ liệu nguồn.

## API

Tiền tố `/api/v1`, dùng Bearer token hiện tại:

| API | Mục đích |
| --- | --- |
| `GET /notifications` | `{items, total, unread_count, limit, offset}`; mới nhất trước |
| `POST /notifications/read` | Body `{"event_ids":[1,2]}`; đánh dấu các sự kiện được phép xem; trả 204 |

Bộ lọc: `unread_only` (mặc định false), `limit` (1–100, mặc định 20), `offset` (>=0), `workspace` (`ADMIN`, `DATA_ADMIN`, `MANAGER`, `STAFF`, tùy chọn). `unread_count` luôn đếm toàn bộ chưa đọc trong workspace hiện tại, không phụ thuộc phân trang hoặc bộ lọc chưa đọc. `total` tính sau bộ lọc.

Mỗi dòng gồm mã sự kiện/yêu cầu, tiêu đề yêu cầu, lĩnh vực/phòng, loại sự kiện, trạng thái sau sự kiện, người thực hiện, thời gian và `read_at`. Không trả mô tả đầy đủ, nội dung hồ sơ nguồn, thông tin kết nối hoặc credential trong thông báo.

Các sự kiện trước khi triển khai cũng được hiển thị nếu người dùng có quyền xem. Thời điểm thông báo là thời gian sự kiện, không phải thời điểm phát hành tính năng.

## Phân quyền

- ADMIN/DATA_ADMIN: các yêu cầu có trong hệ thống.
- Manager: đúng cặp lĩnh vực/phòng phụ trách đang có phân công hợp lệ; bao gồm yêu cầu trả lại người nạp thuộc phòng phụ trách.
- STAFF: yêu cầu thuộc lĩnh vực/phòng đang có phân công, hoặc file đã được duyệt trả về chính người nạp theo quyền hiện có.
- VIEWER: không có quyền nhận thông báo vận hành này.

FE gửi workspace hiện tại để tránh người có nhiều role nhận liên kết không phù hợp với màn hình đang dùng. Workspace không mở rộng quyền. Backend kiểm tra lại vai trò/phân công khi đọc feed và khi đánh dấu đọc; sau khi mất quyền, sự kiện cũ không còn hiển thị và thao tác đọc trả 404.

Trạng thái đọc được lưu tại `app.notification_read`, khóa `(app_user_id, event_id)`; tài khoản khác vẫn giữ trạng thái riêng. Gọi đánh dấu đọc nhiều lần không tạo trùng. Batch tối đa 100 mã; nếu có bất kỳ mã không hợp lệ/ngoài phạm vi thì không đánh dấu mã nào. Không có thao tác tự động đánh dấu toàn bộ lịch sử khi chỉ mở chuông.

Lỗi DB/migration trả `503 NOTIFICATIONS_UNAVAILABLE`. Các phản hồi có `Cache-Control: no-store`.

## Frontend

Chuông thông báo có số chưa đọc, bộ lọc Tất cả/Chưa đọc, phân trang và nút **Đọc trang này**. Polling 15 giây, không dùng WebSocket/push nền. Bấm thông báo mở yêu cầu và gửi đánh dấu đọc; nếu thao tác đọc lỗi, UI báo lỗi và không tự giảm số chưa đọc.

Các đường mở trực tiếp:

- ADMIN: `/admin/quality-issues/{issue_id}`; danh sách `/admin/quality-issues`.
- DATA_ADMIN: `/data/quality/{issue_id}`; danh sách `/data/quality`.
- Manager: `/manager/quality-issues/{issue_id}`; danh sách `/manager/quality-issues`.
- STAFF: `/staff/quality-issues/{issue_id}`; danh sách `/staff/quality-issues`.

Quản trị tiếp tục tạo yêu cầu từ hồ sơ cách ly thật, giao người phụ trách và xác minh bản sửa. Nhân viên tiếp tục nộp bản sửa qua màn hình hiện có. Manager đọc nội dung, vị trí/mã lỗi và lịch sử; tính năng này không cấp thêm quyền ghi/giao việc cho manager.

## Database đang có

```bash
make db-notifications-upgrade
```

Database mới chạy `infra/postgres/init/06-notifications.sh`. Migration thêm bảng trạng thái đọc, không sửa/xóa dữ liệu yêu cầu hiện có.

## Kiểm thử

```bash
.venv/bin/pytest tests/test_notifications.py --no-cov
GOLD_LIVE_TEST=1 .venv/bin/pytest tests/test_gold_live.py::test_manager_reads_current_gold_with_dataset_ownership_and_pagination --no-cov
```

Kiểm tra quyền theo workspace/phòng, phân trang, trạng thái đọc độc lập/idempotent, batch ngoài phạm vi không ghi một phần và thu hồi quyền. Live test dùng database/object riêng.
