# P0 — Baseline chuẩn hóa dữ liệu

## P5 đã triển khai phần tích hợp kỹ thuật

Xem [Silver integration](silver-integration-p5.md) và [checklist G5](g5-checklist.md).
Có Silver-only pipeline, Parquet read-back, manifest, metadata CAS, retry/resume và
CLI rehearsal. Production vẫn chặn bởi G1/DQ; migration chưa áp dụng môi trường thật.

## S4 đã triển khai

Xem [Identity & Value Standardization](identity-values-s4.md) và [checklist G4](g4-checklist.md).
Có engine nhận pending_s4, repository crosswalk bền vững, migration PostgreSQL và
preview ba domain. Hồi quy S1–S4/handoff: 440 tests pass. Chưa áp dụng migration
PostgreSQL hoặc publish Silver; G1 vẫn pending.

## S3 đã triển khai

Xem [chuẩn hóa kiểu dữ liệu](data-types-s3.md) và [checklist G3](g3-checklist.md). Có converter/service nối với S2 và CLI preview; ID/enum bàn giao qua pending_s4 cho S4, chưa publish Silver.

## S2 đã triển khai

Xem [hướng dẫn Schema Mapping](schema-mapping-s2.md) và [checklist G2](g2-checklist.md). Có backend service và CLI preview với sáu ví dụ; chưa chuyển kiểu/resolve ID hoặc publish Silver.

## S1 đã triển khai

Bộ contract Silver draft và kết quả kiểm tra: [G1 checklist](g1-checklist.md), [quy tắc](rules.md), [quan hệ liên domain](cross-domain-links.md). Ba schema ở `silver-schemas/`, mapping ở `mapping-matrix.csv`. S1 chưa được phê duyệt nghiệp vụ; không đổi trạng thái G0.

Ngày kiểm tra: **14/10/2026**. Phạm vi: P0.1–P0.5 trong roadmap, chuẩn bị S1–S4 và P5. Không phải triển khai normalization engine.

Cập nhật sau đối chiếu checkout: xem [review sau pull](post-pull-review.md). Schema không đổi; có reader local mới cần tích hợp metadata và hoàn thiện môi trường test.

## Kết quả và cách sử dụng

1. Đọc [baseline](baseline.md) và [scope matrix](scope-matrix.md) để biết hợp đồng nguồn, phạm vi entity và khoảng trống hiện tại.
2. Dùng [decision log](decision-log.md) làm điểm xuất phát cho thiết kế S1. Quyết định kỹ thuật được chọn cho bản triển khai này; thẩm quyền nghiệp vụ và PII chưa có chữ ký xác nhận.
3. Review [PII matrix](pii-matrix.csv) theo từng field. Đây là chính sách đề xuất, không phải quyền truy cập đã được cấp.
4. Dùng [handoff contract](source-profile-contract.md) để phân biệt cấu hình nguồn và normalization profile.
5. Chạy bộ fixture riêng tại `data/standardization/p0/`. Expected outputs là đặc tả ví dụ, chưa phải kết quả của engine.
6. Theo dõi [G0 và các phụ thuộc](g0-checklist.md). Chỉ đánh dấu G0 PASS khi có bằng chứng còn thiếu.

**Trạng thái: P0 có bộ đầu ra kỹ thuật; G0 chưa PASS.** Docker Engine chưa hoạt động tại thời điểm kiểm tra; chưa có bằng chứng người sở hữu domain duyệt Unified Schema, crosswalk và chính sách PII.

Các đường dẫn tương đối trong bộ tài liệu tính từ repo hoặc từ tài liệu chứa liên kết. Các file nguồn và mẫu legacy được giữ nguyên.
