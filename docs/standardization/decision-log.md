# Decision log P0

Cập nhật 15/10/2026: xem [G1 resolution](g1-resolution.md) cho các lựa chọn kỹ thuật
đã chốt, bằng chứng handoff mới và checklist xác nhận còn mở. Các trạng thái baseline
bên dưới được giữ để phân biệt quyết định kỹ thuật với phê duyệt nghiệp vụ.

Baseline 14/10/2026. `SELECTED` = lựa chọn kỹ thuật cho kế hoạch triển khai, không đồng nghĩa đã code hoặc được chủ nghiệp vụ phê duyệt. `PROPOSED` = cần xác nhận nghiệp vụ trước sử dụng dữ liệu thật. Người chuẩn bị: Codex theo yêu cầu triển khai P0; người xác nhận bên dưới là vai trò cần phân công, chưa phải người đã ký.

| ID | Quyết định và lý do | Trạng thái | Người xác nhận / hạn | Tác động |
|---|---|---|---|---|
| D01 | Dùng `unilake-db-architecture.txt`, Unified SQL và ba JSON làm baseline **nguồn**. Silver có schema/version riêng; không sửa schema nguồn để hợp thức hóa PII hoặc sample legacy. | SELECTED | Tech lead + chủ 3 domain, trước G1 | S1 tạo hợp đồng Silver, giữ PK/FK nghiệp vụ đã review |
| D02 | PK nguồn chỉ duy nhất trong `(source_system namespace, entity, source_key)`. Canonical PK BIGINT cấp bền vững; crosswalk có unique constraint, transaction và phiên bản/bằng chứng. Không dùng hash cắt ngắn làm BIGINT; không gộp theo tên. | SELECTED | Tech lead, trước G1 | S4 cần repository/migration; FK cũng resolve qua crosswalk |
| D03 | Mã nghiệp vụ là string, giữ số 0 đầu. Chuẩn case/trim phải theo field; không coi mã giống nhau giữa hệ thống là bằng chứng tự động cùng người. | SELECTED | Chủ domain, trước G1 | Tách format normalization khỏi identity resolution |
| D04 | PII theo ma trận từng field: Silver analytics mặc định loại direct identifiers và free text nhạy cảm; chỉ giữ thuộc tính cần cho analytics theo phạm vi đã duyệt. Identity matching diễn ra trong vùng hạn chế trước bảo vệ. | PROPOSED | Data Administrator + chủ dữ liệu, trước G1 | Source NOT NULL có thể không tồn tại ở Silver; không ép giá trị giả để qua schema |
| D05 | Chọn **Parquet bất biến + manifest phiên bản** cho story này, theo kiến trúc Data Processing. Delta là phương án tương lai, không triển khai song song hai publisher. Dependency deltalake đang có không chứng minh runtime đã dùng Delta. | SELECTED | Tech lead, trước G1 | P5 ghi staging, read-back rồi công bố snapshot bằng metadata transaction; vẫn cần reconcile |
| D06 | MVP dùng **strict snapshot**: lỗi schema, required value, enum, identity hoặc FK chặn công bố cả snapshot. Vẫn lưu báo cáo/quarantine; warning không làm sai dữ liệu có thể cho qua. Không có ngưỡng phần trăm lỗi ngầm. | SELECTED | Data Administrator, trước G1 | Đơn vị snapshot bao gồm entity cập nhật và phiên bản cha được pin; giữ snapshot cũ nếu thất bại |
| D07 | Thẩm quyền theo entity/field và khoảng hiệu lực, không theo thứ tự file đến. Tạm đề xuất ADM_SYS cho Admissions, ACA_SYS cho Academic, HR_SYS cho HR. Ownership schema không chứng minh nguồn nghiệp vụ có thẩm quyền. | PROPOSED | Chủ 3 domain, trước G1 | Khi chưa có thẩm quyền: ghi SOURCE_CONFLICT, không tự chọn giá trị |
| D08 | Cấu hình connection chỉ chứa thông tin thu thập. Profile chứa schema/mapping/type/enum/identity/PII/error policies, có version bất biến. Binding nguồn–entity–profile phải tường minh. | SELECTED | Tech lead, trước S2 | Không dùng source_type hoặc tên file để chọn mapping |
| D09 | Bronze giữ nguyên, replay tạo run mới và liên kết run cũ. Pin input object version/checksum và toàn bộ rule versions; cùng input và versions không tạo thêm dữ liệu logic. | SELECTED | Tech lead, trước G1 | S2/P5 cần manifest, idempotency key và run lifecycle |

## Điểm mâu thuẫn đã phân xử hoặc còn mở

- Parquet trong kiến trúc và Delta trong tech-stack roadmap: D05 chọn Parquet cho phạm vi story, chưa sửa tài liệu lịch sử thành lời khẳng định hệ thống đã chạy.
- JSON hiện có mô tả source DB, không phải schema Silver sau PII: D01/D04; tránh dùng nguyên JSON làm target.
- `SourceMapping.mapping_for(source_type)` chỉ chọn được một mapping: D08, sửa tại S2.
- `BronzeBatch` chưa bắt buộc source/entity/object version/profile: đề xuất contract riêng; P0 chưa thay runtime.
- Orchestrator hiện nối Silver → Gold, chỉ trả records và tạo run ID sau publish: chuyển sang Silver-only + issue contract ở P5.
- Unified Schema có quan hệ FK nhưng **chưa có bảng đối chiếu mã giữa nhiều nguồn đã được duyệt**: không tự gán crosswalk cho dữ liệu thật.
- Seed mô tả ACA_SYS có program, trong schema owner là Admissions: cần chủ nghiệp vụ xác nhận D07, không lấy seed description làm source priority.

## Quy tắc công bố D05/D06

1. Chọn input snapshot và pin phiên bản các bảng tham chiếu đã công bố.
2. Validate toàn bộ record và quan hệ với snapshot dự kiến; ghi issue và giữ Bronze.
3. Ghi Parquet vào prefix riêng của run; đọc lại kiểm tra kiểu, số dòng, PK/FK và checksum.
4. Tạo manifest bất biến liệt kê object/version/checksum, schema/profile version và các parent snapshot.
5. Transaction PostgreSQL đánh dấu snapshot PUBLISHED và cập nhật con trỏ active với kiểm soát cạnh tranh. Consumer chỉ đọc manifest được metadata đánh dấu PUBLISHED, không scan staging/prefix tùy ý.
6. Nếu storage thành công nhưng metadata thất bại, không công bố; reconcile/retry theo cùng idempotency key. Không coi MinIO và PostgreSQL là một distributed transaction.

Ghi nhận xác nhận: người / vai trò / ngày / ID quyết định / accepted hoặc thay đổi / link bằng chứng. Chưa có xác nhận thì giữ nguyên trạng thái PROPOSED.
