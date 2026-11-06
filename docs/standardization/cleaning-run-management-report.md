# Báo cáo cho team — Theo dõi lượt làm sạch dữ liệu

Data Admin đã có thể tra lịch sử làm sạch dữ liệu, mở chi tiết từng lượt và đọc báo cáo các dòng bị cách ly. Mỗi lần chạy có mã riêng. Nếu chạy lại cùng một job Bronze, team có thể xem hai lượt riêng biệt để đối chiếu kết quả.

| Mục | Team xem được gì |
| --- | --- |
| Danh sách lượt làm sạch | Các lượt mới nhất, có thể lọc theo bộ dữ liệu, job nạp, thời gian và trạng thái |
| Chi tiết một lượt | Số dòng đọc, số dòng đạt, số dòng cách ly, số dòng gộp trùng và số dòng thực sự lên Silver |
| Báo cáo cách ly | Vị trí dòng lỗi, lý do cách ly và phiên bản dữ liệu nguồn để người phụ trách kiểm tra lại |

Một lượt có thể **hoàn tất làm sạch** nhưng **chưa lên Silver**. Ví dụ, hệ thống đọc 100 dòng và xác định 98 dòng đạt, 2 dòng có lỗi. Nếu chính sách của bộ dữ liệu yêu cầu chặn cả lượt, lịch sử vẫn hiển thị 98 dòng đạt, 2 dòng cách ly và 0 dòng đã lên Silver. Data Admin vẫn mở được báo cáo để chuyển thông tin cho phòng sở hữu dữ liệu.

Khi cần xử lý lỗi, Data Admin có thể tìm lượt theo mã job, mở báo cáo để xác định dòng cần kiểm tra, rồi gửi thông tin cho người phụ trách. Sau khi nguồn được sửa và chạy lại, team đối chiếu lượt mới với lượt cũ. Phần này triển khai các chức năng tra cứu ở backend. Giao việc, theo dõi từng hồ sơ lỗi và màn hình thao tác thuộc phần tiếp theo của Quarantine Management.

## Kết quả chạy thử trên môi trường dev

- **Job #5 — Excel đào tạo có ngày sai.** Làm sạch hoàn tất, 1 dòng bị cách ly, Silver bị chặn. Vẫn mở được báo cáo. Mã lượt `b11e6851-5c78-40a1-877c-46c69f3addd3`.
- **Job #6 — Excel danh mục chức vụ hợp lệ.** Làm sạch hoàn tất, 1 dòng lên Silver. Chi tiết hiển thị mã snapshot. Lượt này không có dòng cách ly nên không có báo cáo cách ly. Mã lượt `fddbc109-a3ac-4fa4-99e0-beb332344f58`.

Dữ liệu thử là dữ liệu mẫu. Các ca trên chạy qua API, PostgreSQL và MinIO của môi trường dev.
