# Báo cáo cho team — Hồ sơ dòng dữ liệu bị cách ly

Data Admin đã có thể xem từng dòng bị cách ly sau khi làm sạch dữ liệu, thay vì phải tìm trong báo cáo của cả lượt. Mỗi hồ sơ cho biết dòng nào bị lỗi, sai ở đâu và có thể mở lại giá trị từ đúng bản dữ liệu đã được nạp.

| Việc cần làm | Cách dùng |
| --- | --- |
| Tìm lỗi cần xem | Lọc theo job nạp, lượt làm sạch, bộ dữ liệu, mã lỗi hoặc phòng ban sở hữu dữ liệu. |
| Hiểu nguyên nhân | Mở hồ sơ để xem các trường lỗi, lý do và vị trí trong file nguồn. |
| Đối chiếu với file gốc | Mở dòng nguồn. Với Excel, kết quả chỉ rõ sheet và số dòng trong file. Quyền xem hiện áp dụng cho tài khoản quản trị trên bộ dữ liệu demo. |
| Xem nhóm lỗi phổ biến | Xem thống kê theo bộ dữ liệu, phòng ban và mã lỗi. Một dòng có nhiều lỗi vẫn được tính là một hồ sơ. |

**Ca chạy thử.** Job #5 nạp file Excel mẫu của phòng Đào tạo. Cleaning phát hiện ngày sai ở **dòng 2, sheet `Data`**. Batch bị chặn xuất bản nhưng hồ sơ vẫn xuất hiện trong danh sách, mở được dòng nguồn và được tính vào thống kê. Mã hồ sơ để đối chiếu là `3cba220a-098f-4b93-9e24-eb95ade1879f`.

Khi dữ liệu nguồn được sửa, lượt chạy mới có hồ sơ riêng nếu lỗi vẫn xuất hiện. Team có thể dùng mã lượt để xem lịch sử trước và sau. Phần này cung cấp các chức năng tra cứu ở backend. Phân công, theo dõi khắc phục và màn hình thao tác thuộc các nhóm triển khai tiếp theo.
