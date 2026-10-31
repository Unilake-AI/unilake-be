# G1 — S1 Schema và quy tắc

Version **0.1.0-draft**, 14/10/2026. **Technical contract validated; approval PENDING; activation_allowed=false.** G0 chưa đóng, không bỏ qua dependency bằng cách tự ghi người phê duyệt.

| Checklist | Kết quả / bằng chứng |
|---|---|
| S1.1 Inventory, grain, owner, PK/FK | 22 entity; x-grain/x-owner/x-primary-key; cross-domain-links.md |
| S1.2 Field contract | 3 JSON Schema Draft 2020-12; type/nullability/enum/pattern/precision/scale/physical type và audit |
| S1.3 Rules | rules.md; mapping-matrix.csv: 305 dòng mapping, gồm source fields/drop/derived/audit; 39 luật nghiệp vụ nguồn giữ trong business-rule-review.json |
| S1.4 Quan hệ liên domain | 28 FK; shared/composite key; snapshot dependency order và namespace policy |
| S1.5 Fixtures/checks | 22 expected records; 147 tests PASS tại thời điểm triển khai. Không chạy storage/normalization engine |
| S1.6 Review và khóa version | Chưa duyệt; draft pin SHA-256 source; không activate |

## Quyết định cần xác nhận trước G1 approved

Cập nhật 15/10/2026: [bản chốt thiết kế và hồ sơ quyết định](g1-resolution.md).
Handoff file version/locator đã có code và kiểm thử; S1-B06 đóng phần handoff trong
bộ nhớ, còn mở G0/live storage và DB snapshot. 39 luật đã có phân loại kỹ thuật tại
g1-rule-disposition.json, chưa phải domain approval. Schema vẫn 0.1.0-draft và
activation_allowed=false; không đổi trạng thái G1 thành approved chỉ vì test đạt.

| ID | Cần chốt | Owner / thời hạn |
|---|---|---|
| S1-B01 | D04 PII projection và birth_year; high_school/province/research_field/institution có cần thu gọn tiếp không | Data Administrator + chủ domain, trước approve |
| S1-B02 | Source authority và dictionary/crosswalk nguồn thật | Chủ ba domain, trước activate |
| S1-B03 | Timezone của timestamps naive từng hệ thống | Chủ nguồn, trước activate |
| S1-B04 | Review 39 source DQ rules: sửa mâu thuẫn unique-null; phân loại normalization bắt buộc/downstream, dependency/effective dates | Chủ domain + tech lead, trước approve |
| S1-B05 | Chấp nhận phạm vi v0.1 chưa làm derived KPI ngoài birth_year và strict snapshot | Product owner + Data Administrator, trước approve |
| S1-B06 | Đóng môi trường G0, loader metadata/locator và RDBMS dispatch theo checklist P0 | Ingestion owner, trước nghiệm thu tích hợp |

Tên người xác nhận, ngày và evidence chưa có; không tự điền thay. Có thể bắt đầu code S2 dựa trên draft theo phạm vi được giao; mọi thay đổi được review cần tăng/version hóa contract và cập nhật fixtures.

## Đọc bộ bàn giao

1. silver-schemas/{admissions,academic,hr}.json: validate từng domain snapshot; manifest.json là inventory/approval metadata, không phải row schema.
2. mapping-matrix.csv: baseline source contract → target; không có alias nguồn chưa được cung cấp.
3. rules.md + cross-domain-links.md: semantics và lỗi mà engine phải thực hiện.
4. business-rule-review.json: danh sách luật nguồn chưa được thực thi, tránh mất yêu cầu.
5. backend/tests/test_silver_contracts.py + data/standardization/s1: kiểm chứng độc lập contract.

Tái tạo schema/mapping bằng `python scripts/standardization/build_s1_contracts.py`. Script ghi đè artifact S1 đã sinh; sửa quyết định ở builder trước khi regenerate, review diff. Không dùng script này để cập nhật tự động một version đã approved.
