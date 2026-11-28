# Giám sát Bronze → Silver cho DATA_ADMIN

Trang `/data/auto-pipeline` có tên **Theo dõi Bronze → Silver** trong nhóm Quản lý dữ liệu, ngay sau Nạp dữ liệu. Có lối tắt từ tổng quan Data Admin và từ chi tiết từng lần nạp.

`GET /api/v1/pipeline/auto/flows` dành cho DATA_ADMIN. Query: `search` (tên file, dataset, người nạp), `ingestion_job_id`, `limit` (1–100), `offset`. Response gồm `items`, `next_offset`. Phân trang từ ingestion, vì file còn chờ hoặc lỗi trước Bronze chưa có auto job. Giao diện mặc định tải tất cả lần nạp gần nhất và làm mới mỗi 5 giây; không phải chọn dataset hoặc bật policy trước.

Mỗi dòng thể hiện 5 bước:

1. Tiếp nhận: ingestion đã được tạo. File bị API từ chối trước khi tạo ingestion không nằm trong danh sách này.
2. Lưu Bronze: dùng status/stage và thời điểm của ingestion; chỉ SUCCESS mới được tính hoàn tất.
3. Xếp hàng Silver: auto job đã được tạo, đang chờ hoặc đã được worker nhận. Dataset tạm dừng được ghi rõ; thiếu job không được coi là đã bắt đầu Silver.
4. Làm sạch & kiểm tra: đọc `cleaning_run` thuộc đúng auto job và lần thử gần nhất. Không tách thêm các công đoạn con hoặc bịa % tiến độ khi backend chưa ghi nhận từng công đoạn.
5. Công bố Silver: cần bằng chứng trạng thái PUBLISHED/PUBLISHED_WITH_QUARANTINE và snapshot ID. QUARANTINED/BLOCKED chưa phải công bố thành công.

Chi tiết gồm người gửi, tên file/dataset, số dòng Bronze, đọc/đạt/cách ly/công bố Silver, mã lỗi, lịch thử lại và snapshot. Thời gian hiển thị khi có giá trị đã lưu; backend chưa ghi giờ kết thúc riêng cho cleaning nên không gán giờ công bố cho bước này. Số liệu Silver là bằng chứng của lần thử gần nhất đã ghi nhận, không phải phần trăm tiến độ.

Lịch sử Silver giữ các lần thử cũ và nút thử lại dành cho job FAILED. Các liên kết dẫn tới lần nạp Bronze, báo cáo cleaning và hồ sơ cách ly. Chức năng này chỉ đọc trạng thái vận hành; không tự sửa policy, cấp quyền hay chạy lại file khi mở trang.

Với dữ liệu lịch sử chưa có auto job, monitor đọc lượt `cleaning_run` gần nhất của lần nạp và ghi rõ xử lý trực tiếp. Không tạo auto job giả và không coi file đã có Silver là chưa xử lý. Lượt chỉ làm sạch (`SKIPPED`) được phân biệt với lượt bị chặn bởi chất lượng (`BLOCKED`). Nếu đang có auto job, monitor chỉ nối cleaning của đúng auto job/lần thử để tránh dùng nhầm kết quả của lần thử cũ.
