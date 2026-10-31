# G4 — S4 Identity & Value Standardization

Cập nhật sau P5: đã có phần snapshot validation/audit/Parquet/metadata publication
trong [G5](g5-checklist.md). Các trạng thái P5/P6 còn mở dưới đây là mốc sau S4;
nghiệm thu production vẫn chưa đóng.

15/10/2026: engine, repository và preview đã triển khai; G4 nghiệm thu production
còn mở. G1 chưa approved; activation_allowed=false; không đánh dấu story hoàn tất.

| Mục | Trạng thái / bằng chứng |
|---|---|
| Nhận pending_s4 và pin S3 fingerprint | Có code; từ chối key/version/fingerprint không khớp |
| ID theo namespace + entity + source tuple | Có code; giữ số 0 đầu, không fuzzy matching |
| Canonical BIGINT bền vững | Repository + models + migration; test SQLite file, chưa chạy PostgreSQL |
| Retry và immutable release | Unique source key, savepoint, content fingerprint, chống đổi version |
| FK namespaces | Bắt buộc khai báo từng FK; unknown → REFERENCE_PENDING |
| Shared/composite identity | Lecturer/employee shared ID; employee_qualification hai thành phần |
| Dictionary/version/format | Alias tường minh; NFC/trim/case theo cấu hình; target validation sau mapping |
| Lỗi và lineage | Không ghi raw values vào issue; giữ provenance/locator/lỗi S2/S3 |
| Kiểm thử ba domain | All 22 entities + CSV/Excel thực + CLI flat/nested |
| Source authority và dictionary/crosswalk thật | OPEN — cần evidence G1/D07, fixture không phải approval |
| PostgreSQL migration/concurrency live | OPEN — migration chưa áp dụng |
| Silver acceptance | OPEN — parent snapshot, DQ, audit, physical writer thuộc P5/P6 |

Chi tiết cách dùng, decisions và giới hạn: [S4](identity-values-s4.md).

Kết quả hồi quy: **440 passed** trong nhóm S1–S4/domain/readers/handoff; Ruff đạt,
mypy đạt trên 5 module S3/S4 được kiểm tra. Đây không phải toàn bộ repository suite.
