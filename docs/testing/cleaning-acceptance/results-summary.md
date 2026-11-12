# Kết quả nghiệm thu kỹ thuật local — 12/11/2026

Lần chạy cuối: 2026-11-12T16:38:09.948959+07:00. Python 3.11.9 trên Windows. Bằng chứng: [matrix](latest/matrix.md), [results JSON](latest/results.json), [JUnit](latest/junit.xml). JSON giữ lệnh, dependencies và SHA-256 source/test/schema/migration.

| Phạm vi | PASS | FAIL | SKIP |
|---|---:|---:|---:|
| Matrix admissions | 52 | 0 | 12 |
| Matrix academic | 58 | 0 | 4 |
| Matrix hr | 56 | 0 | 8 |
| Tổng matrix (190 cases) | 166 | 0 | 24 |
| Regression/governance/API ngoài matrix | 431 | 0 | 0 |
| Tổng suite | 597 | 0 | 24 |

24 SKIP là constraint không khai báo ở entity baseline, không phải test thất bại bị đổi trạng thái. Matrix gồm file CSV/XLSX thật qua reader local và component cases; không phải E2E live của mọi nguồn.

Đã kiểm chứng: missing/null/format/type/enum/range và duplicate theo profile; Bronze fixture không đổi; rule version của mapping quarantine; lịch sử revision, owner grant, approve/revoke, source fingerprint; chặn production publish/reuse/resume/activate khi approval không còn hợp lệ; partial cần owner approval và bằng chứng khóa; reuse ghi audit trong transaction authorize.

Ruff check, mypy cho 11 file app thay đổi và git diff --check đạt. Đây là suite chọn lọc, không phải toàn bộ CI. Xem [gap register](gaps.md) và [hướng dẫn production/test lại](production-readiness.md).

**Chưa ký nghiệm thu production:** Docker Linux engine chưa chạy; migration/trigger, tranh chấp giao dịch PostgreSQL, IAM/versioning MinIO và source profiles thực chưa được kiểm chứng live. Chưa cấp owner approval thật hoặc deploy. Các ca live được ghi SKIP riêng trong production-readiness.md.

Mọi rule/approval trong test là synthetic và CẦN DOMAIN OWNER DUYỆT trước khi sử dụng thực tế. Draft source-silver-flow/cleaning-rules không trở thành policy production chỉ vì test PASS.
