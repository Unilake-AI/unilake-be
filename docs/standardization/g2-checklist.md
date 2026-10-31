# G2 — S2 nghiệm thu kỹ thuật

Trạng thái: **S2 kỹ thuật đã kiểm thử; nghiệm thu chuỗi G0/G1/G2 còn phụ thuộc approval và ingestion adapter**. Không tự đổi G1 draft thành approved.

| Công việc roadmap | Kết quả |
|---|---|
| S2.1 Nhiều mapping cùng loại nguồn | MappingRegistry mới theo namespace/domain/entity/source schema/profile version/target version; legacy demo giữ nguyên |
| S2.2 Pin mapping trong handoff | MappingBinding + source_paths/record_locators/input_version; source_loader → BronzeBatch adapter chưa tích hợp (B08) |
| S2.3 Rename/alias/path/ghép/tách | copy/coalesce/concat/split, JSON Pointer; không explode ngầm |
| S2.4 Kiểm tra cấu hình | target coverage/duplicate, path, arity, nguồn khai báo; fail trước row |
| S2.5 Preview | before/after của row đạt, issues theo locator, sample count; không ghi Silver |
| S2.6 Hai cấu trúc/domain | Sáu fixtures expected độc lập + 44 entity-layout đối chiếu contract S1 |

## Bằng chứng

- Backend: schema_mapping.py, mapping_profiles.py; BronzeBatch mở rộng trong ingestion/contracts.py.
- Executable preview: scripts/standardization/preview_s2.py.
- Tests S2 + S1 + domain: **182 passed** tại lượt triển khai. Ruff và mypy module S2 đạt.
- Module boundary suite: 3 lỗi dependency có sẵn; danh sách tại schema-mapping-s2.md. Không sửa unrelated modules để làm xanh số liệu.

## Trước khi sử dụng dữ liệu thật

1. Chốt G1 schema/PII/timezone/authority và profile phiên bản approved.
2. Ingestion owner nối loader với Bronze lưu thật, namespace, physical locator và input version xác minh được. File tạm không phải Bronze bất biến.
3. S3 chuyển kiểu và xử lý derived fields trong vùng được phép; S4 resolve ID/enum. Không đưa giá trị string ID nguồn vào Silver BIGINT trực tiếp.
4. P5 lưu issue/quarantine/lineage và publish sau kiểm tra snapshot; chỉ adapter preview/service hiện tại chưa phải HTTP API cho người dùng cuối.

Không có blocking configuration nào được chuyển thành default âm thầm. Kết quả mapping có lỗi chưa được publish và result luôn silver_ready=false.

