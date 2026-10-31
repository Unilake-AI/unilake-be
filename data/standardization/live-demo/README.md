# Bộ file cho buổi test Bronze → Silver

Tất cả dữ liệu là giả lập, lấy từ fixture S2 của dự án và biến đổi có chủ đích.
Mỗi file có một dòng dữ liệu. XLSX dùng sheet `Data`, header dòng 1, không bỏ dòng.
Mã nguồn, ngày và Decimal được lưu dạng text để đúng profile chuyển đổi.
CSV là UTF-8, phân cách dấu phẩy, các giá trị được đặt trong dấu ngoặc kép.

| File (có cả .xlsx và .csv) | Domain/entity | Kỳ vọng |
|---|---|---|
| 01-admissions | admissions/admission_method | `007` đối chiếu ID, `30,00` → Decimal 30.00, `Có` → true; bỏ description |
| 02-academic | academic/semester | `008` đối chiếu ID, `01/09/2026` → 2026-09-01, `Đang diễn ra` → Ongoing |
| 03-hr | hr/position | `009` đối chiếu ID, `Nhân viên` → Staff |
| 04-bad-date | academic/semester | `31/02/2026` không tồn tại; DATE_PARSE_ERROR; không publish |
| 05-unknown-id | hr/position | `999` chưa có crosswalk; SOURCE_ID_UNMAPPED; không publish |
| 06-hr-updated | hr/position | Cùng mã `009`, tên mới `Giảng viên giả lập cập nhật`; thay dữ liệu entity position |

Canonical ID do PostgreSQL cấp khi khởi tạo crosswalk, không giả định ID cố định.
Hướng dẫn tuần tự: [Buổi test thực tế](../../../docs/standardization/manual-live-demo.md).

Không upload README vào Bronze. Chọn 6 XLSX cho buổi đầu; dùng CSV trong session khác
để kiểm tra reader CSV. Không mở rồi lưu lại CSV bằng Excel trước lần test đầu vì Excel
có thể tự đổi mã `007` thành `7` hoặc đổi ngày tháng.
