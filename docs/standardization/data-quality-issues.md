# Nhóm C — Quản lý yêu cầu xử lý dữ liệu lỗi

| API | Quyền và hành vi |
| --- | --- |
| `POST /api/v1/data-quality-issues` | `ADMIN` hoặc `DATA_ADMIN` tạo yêu cầu và giao phòng ban ngay. Body gồm `title`, `description` tùy chọn, `department_id` kiểu số, `assignee_user_id` tùy chọn, và 1–100 UUID trong `quarantine_record_ids`. Trả `201` và trạng thái `ASSIGNED`. |
| `GET /api/v1/data-quality-issues` | Danh sách theo thời điểm cập nhật mới nhất. Có `department_id`, `assignee_user_id`, `status`, `limit` (1–100) và `offset`. Tài khoản phòng ban chỉ thấy các yêu cầu thuộc phạm vi được giao. |
| `GET /api/v1/data-quality-issues/{issue_id}` | Chi tiết, hồ sơ cách ly liên quan và lịch sử thao tác. Các hồ sơ kèm lý do, mã quy tắc, phiên bản Bronze và vị trí dòng. |
| `PATCH /api/v1/data-quality-issues/{issue_id}/assignment` | `ADMIN` hoặc `DATA_ADMIN` gửi `assignee_user_id` để giao hoặc đổi người phụ trách khi yêu cầu đang `ASSIGNED`. |
| `POST /api/v1/data-quality-issues/{issue_id}/resolution` | Chỉ tài khoản hiện được giao và còn quyền gửi `ingestion_job_id` và `note`. Job phải mới hơn job gốc và lần nộp trước, đã nạp Bronze thành công, thuộc đúng dataset. Chuyển sang `PENDING_VERIFICATION`. |
| `POST /api/v1/data-quality-issues/{issue_id}/verification` | `ADMIN` hoặc `DATA_ADMIN` gửi `cleaning_run_id`, `verified`, `note` và `confirmed_record_ids` nếu xác nhận. Lượt Cleaning phải đã kết thúc và thuộc đúng job sửa. |

Yêu cầu được tạo từ các hồ sơ của cùng dataset. Phòng được giao phải đang hoạt động và phải trùng phòng sở hữu dataset nếu catalog đã khai báo. Khi dataset chưa có chủ sở hữu, người quản trị chọn phòng lúc tạo. Phạm vi phòng ban lấy từ hồ sơ nhân sự đang hoạt động và phân công còn hiệu lực trong PostgreSQL, kết hợp vai trò theo miền nghiệp vụ. Backend đối chiếu quyền trong token với quyền hiện hành trong database. Truy cập chi tiết ngoài phạm vi trả `404`. Chi tiết yêu cầu cung cấp metadata lỗi. Quyền mở dòng Bronze vẫn theo Nhóm B.

```text
ASSIGNED → PENDING_VERIFICATION → VERIFIED
    ↑                │
    └── Từ chối ─────┘
```

Khi xác nhận đã sửa, lượt mới phải đã xuất bản Silver và Data Admin xác nhận đủ UUID các hồ sơ liên kết. Ghi chú nêu khóa nghiệp vụ hoặc căn cứ đối chiếu. Backend kiểm tra bằng chứng ở mức dataset và lượt chạy. Việc đối chiếu đúng từng bản ghi là quyết định thủ công của người xác minh. Khi từ chối, yêu cầu về `ASSIGNED`, lịch sử giữ lý do và lượt dùng để xác minh. Lượt nộp tiếp theo phải có job mới hơn.

Mỗi lần đổi trạng thái khóa dòng yêu cầu trong PostgreSQL và ghi sự kiện trong cùng giao dịch. Yêu cầu đang chờ xác minh hoặc đã xác minh sẽ từ chối thao tác chuyển trạng thái không hợp lệ với `409`. Hồ sơ cách ly cũ vẫn được giữ làm bằng chứng. PATCH đổi người phụ trách trong phòng đã giao và ghi ID cũ/mới vào lịch sử. Người cùng phòng vẫn có thể xem yêu cầu trong phạm vi quyền. Họ không thể báo thay, kể cả khi có quyền quản trị. Lượt chưa có người phụ trách cần được giao trước khi báo đã sửa. Chuyển phòng và xuất CSV/Excel được hoãn.

Database mới dùng DDL khởi tạo. Database hiện có cần migration Nhóm A, B rồi `infra/postgres/migrations/20261106_data_quality_issues.sql`. Áp dụng tiếp `infra/postgres/migrations/20261106_issue_assignees.sql` để thêm người phụ trách. Các yêu cầu cũ giữ `assignee_user_id = null` và giữ lịch sử đã có. Cả hai migration Nhóm C đã áp dụng trên Docker dev.
