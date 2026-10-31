# G3 — S3 Kiểu dữ liệu

Cập nhật 15/10/2026: đã sửa format thiếu thành phần, làm bất biến cấu hình service và kiểm tra contract trước chạy batch. Result có configuration_fingerprint; **295 tests** của nhóm S1–S3/domain đạt, Ruff/mypy module đạt. Các số 237/55 bên dưới là mốc triển khai ban đầu. Kho profile/pinning môi trường và nghiệm thu dữ liệu thật vẫn còn phụ thuộc.

Trạng thái 14/10/2026: **hoàn thành bộ converter/service và kiểm thử kỹ thuật; nghiệm thu tích hợp còn phụ thuộc G0/G1 và dữ liệu nguồn thực tế**.

| Hạng mục | Bằng chứng |
|---|---|
| S3.1 Converter dùng chung/per field | FieldOptions + convert_value, contract S1 làm căn cứ |
| S3.2 Chuyển đổi kiểm soát | Native types, locale/timezone tường minh, phân biệt missing/null/error, không rounding ngầm |
| S3.3 Issue có cấu trúc | TypeIssue + provenance tại TypeResult; không raw PII; lỗi S2 được giữ |
| S3.4 Kiểm thử | 55 tests S3; tổng S1/S2/S3/domain 237 pass; Ruff/mypy module đạt |

Không claim toàn bộ Silver contract đã đạt: PK/FK/enum chờ S4, audit và physical storage check chờ P5. 39 luật nghiệp vụ vẫn cần triển khai theo phân loại G1.

## Trước tích hợp dữ liệu thật

1. Chốt source timezone, formats, separators, boolean/null dictionaries từng field; pin conversion_version cùng input/schema/mapping trong profile được duyệt.
2. Ingestion adapter cung cấp version/locators đúng; Excel phải xác định epoch và có chính sách formula cache, numeric code/float. S3 không thể khôi phục dữ liệu reader đã làm mất.
3. Cung cấp restricted DOB theo version khi dùng birth_year; giữ approval/quyền truy cập của D04.
4. S4 resolve identity và danh mục từ pending_s4, không lấy field string ID nguồn làm canonical key.
5. P5 bổ sung audit, lưu issues/quarantine, đọc lại Parquet để chứng minh physical types và publish snapshot an toàn.

Hướng dẫn: [data-types-s3.md](data-types-s3.md). Chưa có HTTP endpoint mới, persistent conversion registry hoặc Silver writer trong phạm vi S3 này.
