# Gap/bug register — Cleaning acceptance

Nguồn quyết định: code + source profile/schema fixture hiện tại. Mọi policy chưa có bằng chứng owner duyệt đều CẦN DUYỆT. Không gán production approval từ tên test có chữ “approved”.

| ID | Loại / trạng thái | Bằng chứng và ảnh hưởng | Xử lý |
|---|---|---|---|
| BUG-01 | Truy vết / FIXED | `collect_standardization_issues()` lấy `rule_version` trực tiếp từ MappingIssue, nhưng kiểu này không có thuộc tính đó; version nằm ở `IdentityResult.key.mapping_version`. Payload quarantine S2 trước sửa trả null. | Test `test_mapping_quarantine_keeps_pinned_mapping_version` FAIL trước sửa (`mapping-version-before.xml`), PASS trong suite sau sửa. Fallback về đúng pinned MappingKey; không thay policy xử lý dòng. |
| DOC-01 | Tài liệu / OPEN | `source-silver-flow.md` nói chặn toàn lô, không partial. Code `run_manual_cleaning` mặc định partial=true và publisher merge theo PK khi có quarantine report. | README acceptance mô tả code hiện tại. Không sửa draft thành policy đã duyệt; cần owner xác nhận strict/partial và semantics giữ dòng cũ. |
| GOV-01 | IMPLEMENTED / cần live acceptance | Đã thêm immutable revision, explicit owner grant, approve/revoke theo fingerprint, production gate, transaction recheck và manifest/run evidence. Fixture vẫn không phải approval thật. | Test governance tự động; migration PostgreSQL và concurrency/IAM còn SKIP. Chi tiết trong production-readiness.md. |
| COV-01 | Phạm vi / OPEN | Matrix file thật gồm 3 entity baseline CSV/XLSX; supplementary fields/composite keys dùng component fixtures. Strict/partial manual-service regression hiện chủ yếu HR; các domain khác có publication regression chung. | Không tuyên bố mọi source của 22 entity đã acceptance. Khi có profile nguồn thực tế, thêm matrix theo binding/version và chạy live từng domain với strict/partial. |
| ENV-01 | Live / SKIP — chưa chạy | Suite dùng file thật, SQLite/local object store hoặc mock tại ranh giới hạ tầng. Chưa chứng minh MinIO IAM/versioning, PostgreSQL transaction/concurrency và API deploy. | Chuẩn bị checklist live trong README. Không cần credentials production cho local suite. |
| ENV-02 | Windows encoding / MITIGATED | Test existing `definition()` đọc UTF-8 JSON bằng `Path.read_text()` không encoding, gây UnicodeDecodeError dưới CP1252. | Runner đặt `-X utf8` và PYTHONUTF8=1 cho subprocess; không đổi dữ liệu hoặc hash fixture để làm test xanh. |
| ENV-03 | Sandbox / MITIGATED | Faulthandler chỉ ra HTTP TestClient kẹt ở Windows asyncio `_make_self_pipe` → socket.socketpair/accept trong sandbox. | Chạy suite ngoài sandbox với quyền được phép; không cần truy cập mạng production. |
| POLICY-01 | Production guard IMPLEMENTED / remediation CẦN DUYỆT | Engine giữ thứ tự cũ; guard partial production kiểm tra khóa trên batch gốc, chặn nếu có xung đột với quarantine hoặc thiếu bằng chứng khóa. | Thêm 30 ca ba domain, đảo thứ tự. Không chọn winner/điền thiếu; owner chốt remediation và live canonical-identity cases. |
| POLICY-02 | Replay / CẦN DUYỆT | Cleaning replay xác định về dữ liệu, nhưng manual invocation tạo run và audit mới; publication identity gồm parent snapshot. Draft diễn đạt “không nhân đôi issue” rộng hơn behavior này. | Phân biệt retry cùng request publication, manual rerun và lịch sử audit. Không dùng run count làm expected idempotency sai. |

BUG-01 đã được sửa hẹp trong `app/modules/pipeline/cleaning/quarantine.py` và có test hồi quy. Các mục OPEN/CẦN DUYỆT là giới hạn hoặc quyết định cần chốt, không phải chứng cứ mọi lần chạy đang lỗi.

Kết quả máy đọc và log nằm ở `latest/`. Bằng chứng trước sửa được giữ riêng; không cộng ca FAIL lịch sử đó vào số liệu suite sau sửa.

## Bổ sung khi mở rộng production

| ID | Trạng thái | Bằng chứng | Xử lý |
|---|---|---|---|
| GOV-02 | Legacy / giới hạn cần vận hành | Parent snapshot không có approval hoặc đóng góp revision đã lỗi thời bị chặn bảo thủ. | Xây lại scope/snapshot được duyệt; không hạ ENVIRONMENT để vượt gate. |
| ENV-04 | Docker / live BLOCKED | Ngày 12/11/2026 Docker CLI không tìm thấy pipe dockerDesktopLinuxEngine. | Khởi động Docker Desktop Linux engine, chuẩn bị staging và chạy matrix live trong production-readiness.md. |

BUG-02 (FIXED): fast reuse trước đây trả receipt trước khi callback ghi cleaning run. Đã đưa callback vào cùng transaction authorize, giữ khóa approval đến khi audit commit; có regression test.
