# Source configuration và normalization profile

Baseline đề xuất P0 bên dưới. Cập nhật 15/10/2026: file handoff trong bộ nhớ đã
triển khai qua `read_data_source_batch` / `read_file_snapshot` / `reader_to_batch`;
xem [G1 resolution](g1-resolution.md) cho contract thực tế. Chưa có ORM/API job,
Bronze persistence hoặc replay bền vững. CSV locator là ordinal record sau parse,
không phải dòng vật lý; Excel có sheet/dòng vật lý.

## Điểm tích hợp với reader hiện có (review 14/10/2026)

Working tree có `read_data_source(repo, data_source_id, source_table)` trả `ReadResult(columns, rows, source_table)`. Tái sử dụng reader; bổ sung adapter ở ingestion để mang source_system_id/data_source_id/dataset/entity/profile tới BronzeBatch. Không đưa mapping/PII vào reader và không coi source_table là entity ID.

Adapter phải giữ locator vật lý trước khi parser bỏ dòng/trim header, cũng như original object key/version/checksum trước khi MinIO resolver xóa file tạm. Hiện resolver chỉ tải object hiện hành; replay chưa được đảm bảo. Excel/PostgreSQL có thể trả date/number native trong khi CSV trả string, vì vậy type converter cần xử lý cả hai và khai báo cách xử lý Excel formula cache/number formatting. Bronze gốc vẫn phải lưu nguyên file; parsed rows không thay thế bằng chứng nguồn.

RDBMS trong catalog chưa được request_builder/factory hỗ trợ trực tiếp. Phải resolve engine rõ ràng hoặc báo unsupported trước chạy; không tự suy RDBMS = PostgreSQL. Dependencies openpyxl và DuckDB postgres extension là điều kiện môi trường của loader, không phải normalization rule.

## Source configuration

`data_source_id` xác định cấu hình kết nối; `source_system_id` xác định hệ thống nguồn bền vững. Nhiều cấu hình upload/API/DB có thể cùng một hệ thống. Thay cấu hình không tạo namespace ID mới. FILE_SHARE chỉ là nơi vận chuyển: phải khai báo hệ thống phát sinh dữ liệu, không dùng FILE_SHARE làm namespace chung cho mọi file.

Không chứa quy tắc chuyển đổi và không nhúng credentials vào profile, log hoặc manifest.

## Binding bắt buộc

| Trường | Ý nghĩa |
|---|---|
| source_system_id / namespace | Danh tính hệ thống phát sinh, ổn định và được đăng ký |
| data_source_id, dataset_id | Cấu hình thu thập và dataset logic |
| domain_id, entity | Owner và mức hạt; xác nhận bằng registry |
| source_schema_version | Cấu trúc nguồn đã nhận diện |
| normalization_profile_id, version | Phiên bản profile active tường minh |

Không có đúng một binding thì chặn trước chạy (`PROFILE_NOT_FOUND` / `PROFILE_AMBIGUOUS`). Thay đổi cấu trúc phải validate compatibility, không tự đoán entity từ tên file.

## Profile bất biến sau kích hoạt

Profile tham chiếu target schema version; field mapping; locale, timezone, date formats và null tokens per field; enum dictionary version; crosswalk policy version; authority policy; PII policy; strict snapshot policy. DRAFT → VALIDATED → ACTIVE → RETIRED. Preview không kích hoạt profile và không ghi Silver. Run đang chạy giữ version cũ khi profile mới active.

## Bronze handoff cần mở rộng tại S2

Ngoài `batch_id`, `domain_id`, `source_type`, `records` hiện có: bắt buộc binding ở trên, bucket/object key/version/checksum, source record locator, ingestion timestamp, schema fingerprint. Nếu object storage không có version phải có bất biến theo key và checksum. Namespace FK được khai báo riêng khi FK trỏ hệ thống khác; không mặc định mọi FK thuộc hệ thống của record con.

Run pin toàn bộ tham chiếu. Locator CSV là ordinal record, Excel là sheet/dòng vật lý,
JSON là path; luôn đi cùng input version và parser fingerprint. Không lấy giá trị PII
làm locator. Lỗi gồm run/batch/locator/entity/field/rule/version/code/severity; raw value
nhạy cảm không ghi vào log thường.
