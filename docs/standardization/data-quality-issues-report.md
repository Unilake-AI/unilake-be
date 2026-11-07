# Báo cáo cho team — Giao và xác minh lỗi dữ liệu

Data Admin đã có thể gom một hoặc nhiều dòng lỗi của cùng bộ dữ liệu thành một yêu cầu, giao cho phòng ban và chọn người phụ trách và theo dõi đến khi xác minh xong. Thành viên có quyền trong phòng xem yêu cầu được giao. Chỉ người phụ trách hiện tại được gửi mã job của bản đã sửa.

| Bước | Ai làm | Kết quả |
| --- | --- | --- |
| Giao lỗi | Data Admin chọn các dòng lỗi, phòng ban và người phụ trách | Yêu cầu ở trạng thái **Cần xử lý** |
| Báo đã sửa | Người phụ trách gửi ghi chú và job nạp bản sửa | Yêu cầu chuyển sang **Chờ xác minh** |
| Xác minh | Data Admin đối chiếu đúng bản ghi với kết quả chạy mới | Đạt thì **Đã xác minh**. Nếu vẫn sai, yêu cầu trở về **Cần xử lý** kèm lý do |

Mỗi thao tác lưu người thực hiện, thời gian và ghi chú. Ví dụ, Đào tạo sửa ngày bắt đầu học kỳ. Nếu file nộp lại vẫn sai, Data Admin trả yêu cầu về phòng với lý do cụ thể. Sau khi nạp bản sửa tiếp theo và dữ liệu đã lên Silver, Data Admin đối chiếu theo mã học kỳ rồi xác minh. Lịch sử giữ cả lần bị trả lại và lần được chấp nhận.

Phạm vi MVP là giao ngay cho phòng khi tạo và tra cứu tiến độ. Phiên bản này cung cấp chức năng ở backend. Data Admin có thể đổi người phụ trách khi yêu cầu còn cần xử lý. Chuyển phòng và tải Excel/CSV để xử lý hàng loạt được hoãn.

## Kết quả chạy thử trên môi trường dev

Yêu cầu mẫu `8202165b-65a2-4a58-a1c5-2c8b0d6d7543` được giao cho phòng Đào tạo demo. Người ở phòng khác không thấy yêu cầu. Bản nộp thứ nhất (job #9) vẫn có lỗi và bị trả về. Bản nộp thứ hai (job #10) lên Silver và yêu cầu chuyển sang **Đã xác minh**. Cả hai lần nộp và lý do trả lại đều có trong lịch sử.

Sau khi bổ sung giao theo cá nhân, ca thử xác nhận người cùng phòng và Data Admin không thể báo thay. Người phụ trách mới có thể nộp bản sửa, và việc bàn giao được lưu trong lịch sử.
