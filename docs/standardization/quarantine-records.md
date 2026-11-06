# Nhóm B — Tra cứu hồ sơ cách ly

| API | Hành vi |
| --- | --- |
| `GET /api/v1/quarantine-records` | Liệt kê theo lượt mới nhất rồi đến vị trí bản ghi. Lọc theo `cleaning_run_id`, `ingestion_job_id`, `dataset_id`, `owner_department_id`, `error_code`. Dùng `limit` 1–100, mặc định 20, và `offset` mặc định 0. |
| `GET /api/v1/quarantine-records/{record_id}` | Trả hồ sơ theo UUID, các quy tắc vi phạm, vị trí dòng và metadata của bản Bronze. |
| `GET /api/v1/quarantine-records/{record_id}/source` | Đọc dữ liệu theo phiên bản Bronze đã ghim. Trả tên cột nguồn, vị trí, các trường lỗi và giá trị đọc từ CSV/Excel trước Cleaning. |
| `GET /api/v1/quarantine-records/summary` | Dùng cùng bộ lọc với danh sách, tính tổng hồ sơ và số hồ sơ theo dataset, phòng ban sở hữu và mã lỗi. Thống kê tính trên toàn bộ kết quả lọc. |

Bốn API dành cho `ADMIN` và `DATA_ADMIN`. Phòng ban dùng để lọc là phòng đang sở hữu dataset trong catalog. Dataset chưa có phòng sở hữu nằm trong nhóm `owner_department_id: null`. Một hồ sơ có nhiều mã lỗi xuất hiện ở nhiều nhóm mã, nhưng chỉ được tính một lần trong tổng hồ sơ. Bộ lọc mã lỗi chọn hồ sơ chứa mã đó. Thống kê theo mã vẫn phản ánh các mã còn lại trên các hồ sơ đã chọn.

## Ghi và truy vết

Cleaning lưu một hồ sơ cho mỗi locator bị cách ly, với ràng buộc duy nhất theo lượt và locator. Hồ sơ giữ danh sách lỗi thực tế do Cleaning ghi nhận, có mã, trường, phiên bản quy tắc và vị trí. Cùng dòng xuất hiện ở lượt khác có hồ sơ mới. Cảnh báo chuẩn hóa hoặc dòng đã gộp trùng không tạo hồ sơ cách ly.

Thông tin đọc lại Bronze được lưu một lần trên `cleaning_run`, gồm URI, phiên bản object, checksum, cấu hình và dấu vân tay reader. Các hồ sơ và thống kê Cleaning được ghi trong cùng giao dịch PostgreSQL trước khi tạo báo cáo MinIO và trước khi xuất bản Silver. Lượt bị chặn vẫn giữ các hồ sơ. Nếu giao dịch lưu hồ sơ thất bại, pipeline dừng trước Silver. Lỗi chuẩn hóa S2–S4 và lỗi xuất bản vẫn được quản lý theo lượt chạy Silver.

## Xem dòng nguồn

Backend giới hạn xem dòng Bronze ở các dataset demo đang hoạt động, với danh sách cột được phép cho từng entity. Kết quả không lưu vào HTTP cache. Backend dùng tài khoản Bronze chỉ đọc, kiểm tra phiên bản, kích thước, SHA-256, dấu vân tay parser/cột và locator trước khi trả dữ liệu. Nếu đổi phiên bản thư viện parser làm dấu vân tay lệch, API từ chối trả dòng để tránh nhầm dữ liệu.

`record_position` là thứ tự bản ghi đã đọc, bắt đầu từ 1. Excel trả thêm `sheet_name` và `excel_row_number` lấy từ locator đã lưu. CSV không trả số dòng vật lý vì một bản ghi có thể trải qua nhiều dòng văn bản.

Bản đầu đọc toàn file với giới hạn 8 MiB và giới hạn 32 MiB cho Excel sau giải nén. Timeout kết nối/đọc và mốc thời gian xử lý đặt 15 giây. Reader chạy trong luồng nền và mốc này được kiểm tra trước/sau parser, chưa ngắt cưỡng bức parser. File vượt giới hạn trả `413`. Bronze bị thiếu, sai phiên bản hoặc không xác minh được trả `503`.

Database hiện có áp dụng migration `20261106_cleaning_runs.sql` trước `20261106_quarantine_records.sql`. Database mới tạo bảng từ DDL khởi tạo. Hồ sơ bắt đầu từ lượt chạy với code mới. Có thể chạy lại Cleaning trên phiên bản Bronze còn lưu để tạo lượt và hồ sơ mới.

[Báo cáo cho team](quarantine-records-report.md)
