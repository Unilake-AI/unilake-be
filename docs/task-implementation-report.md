# Báo cáo xử lý task kiểm thử chất lượng dữ liệu và kiểm soát Cleaning trước production

**Ngày báo cáo:** 12/11/2026, múi giờ Asia/Ho_Chi_Minh.  
**Dự án:** UniLake BE — `D:\Caps1\unilake-be`.  
**Kết luận hiện tại:** Đã triển khai bộ kiểm thử, sửa lỗi truy vết và bổ sung cơ chế kiểm soát production trong backend. Đã kiểm chứng bằng bộ test local chọn lọc. **Chưa đủ bằng chứng để ký nghiệm thu hoặc phát hành production.**

## 1. Yêu cầu ban đầu và phạm vi được mở rộng

Task yêu cầu kiểm thử missing/null, invalid format/type, enum/range, exact duplicates và conflicting business keys theo source profile của ba domain admissions, academic, hr; kiểm tra batch reject/row quarantine, tính bất biến Bronze và provenance/rule version. Deliverables gồm test cases, test results và danh sách gap/bug, không triển khai lại task Cleaning đã Done ở Sprint 4.

Sau đó, phạm vi được mở rộng theo yêu cầu: không chỉ viết test cho các ví dụ cụ thể mà phải xử lý các điều kiện để chức năng có thể được vận hành production. Vì vậy, phần thực hiện được chia thành hai lớp:

1. **Kiểm chứng chức năng hiện có:** tái sử dụng engine Cleaning và các regression tests; thêm matrix có input, expected, actual, PASS/FAIL/SKIP.
2. **Kiểm soát trước khi công bố:** lịch sử profile, quyền domain owner, approve/revoke theo revision/fingerprint, kiểm tra lại approval trong transaction, bằng chứng publication và chặn partial khi không đủ bằng chứng khóa.

Phạm vi thay đổi là backend, migration, test và tài liệu. Chưa triển khai giao diện phê duyệt mới ở frontend, chưa tự động hóa toàn bộ Bronze → Silver, chưa triển khai Silver business rows vào PostgreSQL và chưa deploy dịch vụ.

## 2. Căn cứ và mức độ tin cậy của báo cáo

Code, source profile và schema được dùng để xác định behavior thực tế. `source-silver-flow` và `cleaning-rules` được dùng để đối chiếu, không được coi là policy production đã được domain owner duyệt.

Lần kiểm thử được báo cáo chạy lúc **16:38:09 ngày 12/11/2026**, Python 3.11.9 trên Windows. Đây là kết quả của runner, không phải số lượng test suy ra từ việc đọc code.

Khi lập báo cáo, đã đối chiếu lại **283 file** được ghi SHA-256 trong `latest/results.json`: không có file thiếu hoặc khác hash so với evidence của lần chạy đó. HEAD hiện tại là `1ee2f7d` — `feat: add pipeline cleaning, quarantine, governance, and acceptance testing modules`. Evidence lưu base commit tại thời điểm chạy là `92deb39e85c2cf250eeea2f4c3c21253c101bcbf`; hash từng file được dùng để đối chiếu nội dung thay vì chỉ dựa vào commit ID.

Báo cáo này không chạy lại toàn bộ test và không thực hiện thêm deployment. Kết quả Ruff, mypy và diff check là các kiểm tra đã chạy ở lượt triển khai trước.

## 3. Luồng xử lý được kiểm chứng

Luồng hiện tại được đọc và kiểm thử theo thứ tự:

`Ingestion thành công → Bronze đã pin version/checksum → source profile → kiểm tra binding/layout → missing → format → duplicate → mapping/type/identity → quarantine/report → kiểm tra điều kiện publication → Silver snapshot/manifest/head`.

Các phân biệt quan trọng:

- **Batch reject:** nguồn, binding, layout hoặc bằng chứng Bronze không hợp lệ thì không tiếp tục công bố dựa trên batch đó. Không cố đoán lại layout hoặc đọc một object version khác cho đủ dữ liệu.
- **Row quarantine:** lỗi dữ liệu ở cấp dòng được lưu với locator và rule liên quan; các dòng khác vẫn có thể qua Cleaning.
- **Strict publication:** có dòng quarantine thì không công bố tập dòng còn lại.
- **Partial publication:** chỉ được công bố tập hợp lệ khi đáp ứng policy và kiểm tra an toàn. Code hiện có merge theo PK và giữ dòng parent không bị thay; đây không phải phép append vô điều kiện.
- **Cleaning hoàn tất không đồng nghĩa đã publish.** Cần đọc cả cleaning status, publication status, snapshot và report.

Bronze vẫn là dữ liệu gốc. Cleaning tạo kết quả xử lý và evidence riêng, không sửa file Bronze để làm dữ liệu trở nên hợp lệ.

## 4. Đối chiếu từng yêu cầu và acceptance criteria

| Yêu cầu | Đã thực hiện | Bằng chứng | Giới hạn / trạng thái |
|---|---|---|---|
| Matrix theo ba domain | Có domain/entity, input, expected, actual và PASS/FAIL/SKIP | `test_cleaning_acceptance.py`, `latest/matrix.md`, `latest/results.json` | Đạt ở phạm vi fixture; không đại diện cho mọi source production |
| Missing/null | Required null, nullable null, token đã/chưa cấu hình, phân biệt thiếu giá trị với thiếu cột | Matrix và regression missing values | Không tự coi mọi chuỗi như `N/A` là null |
| Invalid format/type | Kiểu số, ngày và các lỗi chuyển đổi; alias/format chỉ theo cấu hình | Matrix, format/type regressions | Không tự sửa giá trị theo suy đoán |
| Enum/range | Giá trị hợp lệ, không hợp lệ, tại biên và ngoài biên khi schema khai báo | Matrix và supplementary field tests | Thiếu constraint thì SKIP, không tự tạo policy |
| Exact duplicates | Kiểm tra mặc định và cấu hình collapse tường minh | Duplicate tests và source-profile matrix | Bật flag trong fixture không có nghĩa đã được owner duyệt |
| Conflicting business keys | Kiểm tra PK, unique/business keys, khóa ghép và đảo thứ tự | Matrix, component/regression tests | Không dùng first/last/most-complete để giải quyết conflict |
| Không chọn winner khi thiếu bằng chứng | Thêm guard kiểm tra khóa source trên batch gốc trước partial production | 30 ca mới; `partial_safety.py` | Guard khóa source không thay thế acceptance canonical identity/crosswalk |
| Batch reject vs row quarantine | Kiểm tra lỗi binding/header/checksum/version/row count và batch trộn dòng tốt/xấu | Source-cleaning và reader tests | Cần chạy lại trên API/worker/storage thật |
| Bronze giữ nguyên | So sánh bytes file fixture trước/sau, kể cả nhánh lỗi; kiểm tra pinned provenance | CSV/XLSX qua reader local | Chưa chứng minh IAM/versioning MinIO thật |
| Error code, locator, rule version | Sửa mapping quarantine bị thiếu version; bổ sung evidence run/report/manifest | Regression bug và governance tests | Lịch sử cũ có thể chưa có field evidence mới |
| Rule chưa duyệt phải đánh dấu | Fixture ghi `NEEDS_DOMAIN_OWNER_APPROVAL`; profile API có approval status; production chặn rule chưa duyệt | API và governance tests | Không cấp approval thật trong quá trình làm task |
| Không trùng Cleaning Sprint 4 | Giữ engine, thứ tự xử lý và tái sử dụng test cũ; bổ sung test/guard/governance tại các điểm còn thiếu | Phạm vi file thay đổi | Không viết lại chức năng Cleaning hoặc UI cũ |

## 5. Các thay đổi backend đã thực hiện

### 5.1. Sửa lỗi mất rule version ở mapping quarantine

Trước sửa, phần thu thập issue lấy `rule_version` trực tiếp từ `MappingIssue`, trong khi kiểu issue này không có thuộc tính đó. Version cần dùng nằm ở `IdentityResult.key.mapping_version`.

Đã bổ sung fallback về mapping version đã pin cho issue ở giai đoạn mapping. Không lấy version hiện hành tùy ý và không thay đổi disposition của dòng.

Test `test_mapping_quarantine_keeps_pinned_mapping_version` có evidence FAIL trước sửa trong `mapping-version-before.xml` và PASS sau sửa. FAIL lịch sử không được cộng vào kết quả suite cuối.

### 5.2. Lưu lịch sử profile và ràng buộc approval với đúng nội dung

Mỗi lần tạo/sửa profile lưu một revision gồm definition, fingerprint, người tạo và thời gian. Việc cập nhật current profile và thêm revision nằm trong cùng transaction.

Approval yêu cầu đúng revision và fingerprint. Khi profile đổi, revision mới cần được duyệt lại. Profile từ file không tự trở thành profile được duyệt cho production.

PostgreSQL migration có trigger chặn UPDATE/DELETE lịch sử revision. Trigger đã được viết nhưng **chưa được kiểm chứng thực thi trên PostgreSQL thật** trong phiên làm việc này.

### 5.3. Cấp quyền owner, approve và revoke

ADMIN cấp grant theo domain cho tài khoản có vai trò MANAGER tương ứng. DATA_ADMIN không được tự coi là domain owner chỉ vì có quyền sửa cấu hình.

Khi duyệt, hệ thống kiểm tra tài khoản active, vai trò phù hợp, grant còn hiệu lực, đúng domain và người duyệt khác người sửa profile. Request phải có evidence và quyết định `allow_partial_publication` tường minh.

Approval hoặc grant có thể bị thu hồi, kèm người thực hiện, thời điểm và lý do. Approval đã thu hồi không được bật lại tại cùng revision; cần tạo revision mới. Đây là cơ chế kiểm soát được triển khai, không phải bằng chứng tổ chức đã chỉ định hoặc phê duyệt một owner thật.

### 5.4. Chặn publication không đủ điều kiện

Môi trường ngoài `development`, `local`, `test` bắt buộc kiểm tra approval; bao gồm `production`, `staging` và tên môi trường không nhận diện. Cấu hình mặc định vẫn là development, vì vậy deployment phải đặt ENVIRONMENT đúng.

Kiểm tra được nối vào publish, reuse, resume và activate snapshot. Manifest phải có approval hợp lệ cho mapping đóng góp dữ liệu, đúng scope và schema fingerprint. Không được lấy approval của mapping này để hợp thức hóa mapping khác.

Việc kiểm tra lại trong transaction sử dụng khóa profile, approval và grant theo thứ tự xác định. Vai trò/tài khoản owner cũng được đọc có khóa chia sẻ. Mục đích là giữ bằng chứng phê duyệt ổn định đến khi commit. **Kiểm thử SQLite không chứng minh semantics khóa PostgreSQL; phần concurrency vẫn cần live test.**

### 5.5. Sửa khoảng trống transaction ở fast reuse

Đường fast reuse trước đó có thể trả receipt trước khi callback ghi kết quả cleaning run. Nếu approval bị thu hồi giữa hai bước, evidence và trạng thái cleaning có thể không được ghi cùng lần kiểm tra quyền.

Đã đưa callback của fast reuse vào cùng transaction authorize. Có regression kiểm tra callback được gọi trong transaction cho cả publish mới và reuse.

### 5.6. Kiểm tra khóa với các dòng đã bị loại sớm

Engine cũ chạy missing → format → duplicate. Một dòng bị quarantine ở missing/format không còn tham gia bước duplicate. Nếu chỉ nhìn các dòng còn lại, partial publication có thể bỏ qua xung đột khóa với dòng đã bị loại.

Đã thêm `partial_key_evidence()` cho admission guard production:

1. Lấy các khóa được khai báo trong duplicate profile.
2. Áp dụng null/format rules của các trường khóa trên toàn batch gốc, độc lập với lỗi ở trường không phải khóa.
3. Đối chiếu nhóm khóa với tập locator đã bị quarantine.
4. Nếu còn dòng chia sẻ khóa với dòng quarantine, trả `PARTIAL_KEY_CONFLICT_WITH_QUARANTINE`.
5. Nếu thiếu hoặc không kiểm chứng được bằng chứng khóa, trả `PARTIAL_KEY_EVIDENCE_INCOMPLETE`.
6. Chặn publication; không điền dữ liệu và không chọn một dòng thắng.

Ví dụ: hai dòng cùng mã, một dòng thiếu tên. Dòng đầy đủ có thể qua Cleaning nhưng không được tự động công bố một phần nếu guard phát hiện xung đột khóa với dòng thiếu tên. Report giữ locator của dòng liên quan và rule version. Số liệu “clean” không bị đổi thành “published”.

Manifest partial production phải có kiểm tra khóa `PASSED`, đúng phiên bản kiểm tra và rule version, không còn blocker. Bộ test bổ sung 30 ca trên ba domain, gồm lỗi non-key, khóa không đủ bằng chứng, khóa phân biệt và đảo thứ tự dòng.

### 5.7. Bổ sung evidence và response API

Cleaning run có `profile_evidence` để lưu definition, fingerprint, rule version và approval đã dùng. Report và manifest giữ bằng chứng tương ứng; approval tham gia publication request hash.

Response manual cleaning bổ sung `profile_id`, `profile_fingerprint`, `profile_approval`, `partial_key_check`. API lịch sử cleaning có `profile_evidence`; dữ liệu cũ có thể null. Profile API có `approval_status` và endpoint đọc revision/approval history.

## 6. Database và API được bổ sung

Migration: `infra/postgres/migrations/20261112_silver_profile_governance.sql`. Fresh-init schema cũng đã được cập nhật.

| Thành phần | Mục đích |
|---|---|
| `app.silver_profile_revision` | Giữ definition và fingerprint của từng revision |
| `app.silver_domain_owner` | Giữ grant, người cấp và thông tin thu hồi |
| `app.silver_profile_approval` | Approval theo profile/revision, evidence, partial policy và thông tin thu hồi |
| `app.cleaning_run.profile_evidence` | Evidence cấu hình đã dùng trong run |
| Trigger `silver_profile_revision_immutable` | Chặn UPDATE/DELETE revision trên PostgreSQL |

Migration backfill revision hiện có từ current profile nhưng không tự approve và không thể phục dựng lịch sử đã bị ghi đè trước đây.

Các endpoint dưới đây dùng prefix API mặc định `/api/v1`:

| Method và path | Quyền / mục đích |
|---|---|
| `POST /pipeline/profiles/owners/grants` | ADMIN giao quyền owner theo domain |
| `POST /pipeline/profiles/owners/grants/{grant_id}/revoke` | ADMIN thu hồi grant |
| `GET /pipeline/profiles/{profile_id}/revisions` | Đọc revision và approval history theo quyền kiểm tra trong API |
| `POST /pipeline/profiles/{profile_id}/approve` | Owner đủ điều kiện duyệt revision/fingerprint chính xác |
| `POST /pipeline/profiles/{profile_id}/revisions/{revision}/revoke` | Người đã duyệt hoặc ADMIN thu hồi approval, theo kiểm tra quyền hiện hành |

Owner có thể xem lịch sử theo domain. Preview hiện vẫn sử dụng quyền ADMIN/DATA_ADMIN; nếu owner chỉ có MANAGER, cần nhận output preview/test từ người có quyền hoặc dùng tài khoản có quyền phù hợp. Không mặc nhiên mở quyền preview cho tất cả manager trong task này.

Thu hồi approval chặn thao tác công bố/kích hoạt tiếp theo; không tự xóa dữ liệu hoặc thay active head. Việc rút dữ liệu đang active khỏi sử dụng cần quyết định vận hành riêng.

## 7. Bộ kiểm thử và kết quả thực tế

Runner thực thi 19 module test liên quan. Tổng cộng **621 kết quả: 597 PASS, 0 FAIL, 24 SKIP**.

| Phạm vi | PASS | FAIL | SKIP |
|---|---:|---:|---:|
| Matrix admissions | 52 | 0 | 12 |
| Matrix academic | 58 | 0 | 4 |
| Matrix hr | 56 | 0 | 8 |
| Tổng matrix — 190 cases | 166 | 0 | 24 |
| Regression/governance/API ngoài matrix | 431 | 0 | 0 |
| Tổng suite | **597** | **0** | **24** |

190 case matrix gồm:

- 144 tổ hợp: 24 scenario × 3 domain × CSV/XLSX. Baseline là admissions/admission_method, academic/course và hr/position.
- 16 supplementary component cases cho field/khóa ghép ở entity phù hợp.
- 30 component cases cho guard khóa qua các bước xử lý và đảo thứ tự dòng.

24 SKIP xảy ra khi entity baseline không có constraint đang được xét, ví dụ nullable hoặc numeric bound. Không tạo enum/range giả để biến SKIP thành PASS. Không có assertion thất bại bị chuyển thành SKIP để làm suite xanh. Các ca live chưa chạy được ghi riêng, không nằm trong 24 SKIP này.

Các mức kiểm chứng:

| Mức | Đã làm được | Chưa được chứng minh |
|---|---|---|
| Component | Rule, lỗi, locator, version, duplicate và guard | Phân quyền object storage thật |
| Local integration | Reader CSV/XLSX thật, SQLite, local object store, luồng publication/manifest | Transaction và lock behavior PostgreSQL |
| API in-process | RBAC, profile lifecycle, approve/revoke, activation và response | API deploy, network, token/session trên môi trường staging thực |
| Static checks | Ruff check; format check 18 file; mypy 11 file app; diff check | Không thay thế runtime acceptance |

Đây không phải toàn bộ test suite của backend hoặc toàn bộ CI. PASS local không xác nhận mọi source của 22 entity đã được nghiệm thu.

## 8. File bàn giao và vai trò

| File / nhóm file | Nội dung |
|---|---|
| `tests/test_cleaning_acceptance.py` | Matrix dữ liệu ba domain và guard khóa |
| `tests/test_silver_profile_governance.py` | Lifecycle, quyền owner, revoke, production admission và partial evidence |
| `tests/test_silver_management_api.py` | Regression profile/snapshot API; bổ sung schema fixture và sửa đường dẫn dài Windows |
| `tests/test_quarantine_records.py` | Regression mapping rule version |
| `scripts/run_cleaning_acceptance.py` | Chạy suite, xuất matrix/JSON/JUnit/log và source hashes |
| `app/modules/pipeline/cleaning/quarantine.py` | Sửa version của mapping issue |
| `app/modules/pipeline/cleaning/partial_safety.py` | Kiểm tra khóa source trước partial production |
| `app/repositories/silver_governance_repo.py` | Kiểm tra owner/approval, evidence, manifest và thứ tự khóa |
| `app/modules/pipeline/models/profiles.py`, `models/core.py`, `models/__init__.py` | Model và khai báo evidence/lifecycle mới |
| `app/modules/pipeline/routers/profiles.py` | API cấp grant, history, approve/revoke và trạng thái profile |
| `app/modules/pipeline/routers/pipeline.py`, `routers/cleaning_runs.py` | Response/evidence của cleaning |
| `app/modules/pipeline/routers/snapshots.py` | Gate khi kích hoạt snapshot |
| `app/modules/pipeline/services/source_cleaning.py` | Nối approval, guard và evidence vào luồng manual cleaning |
| `app/modules/pipeline/silver/publication.py`, `repositories/silver.py` | Kiểm tra publication/reuse/resume trong transaction |
| `infra/postgres/migrations/20261112_silver_profile_governance.sql`, `infra/postgres/init/02-schema.sql` | Migration và schema fresh install |
| `docs/testing/cleaning-acceptance/` | Hướng dẫn, kết quả, evidence và gap register |

## 9. Gap và việc chưa hoàn thành

| Mục | Trạng thái và ảnh hưởng | Điều kiện xử lý tiếp |
|---|---|---|
| PostgreSQL migration/trigger/concurrency | Chưa chạy live; SQLite không chứng minh khóa và trigger PostgreSQL | Môi trường staging, backup và test transaction thật |
| MinIO IAM/versioning | Chưa chứng minh reader không ghi/xóa và đọc đúng object version trên server thật | MinIO staging với tài khoản từng service và versioning |
| Source profiles thực | Fixture chỉ minh họa cấu hình tường minh | Owner cung cấp/xác nhận profile cho từng nguồn sẽ release |
| Business policy | Null token, enum/range, keys, collapse, strict/partial, retention còn cần duyệt | Biên bản/ticket và approval revision/fingerprint thật |
| Canonical identity/crosswalk | Guard mới đối chiếu khóa source; không thay toàn bộ kiểm chứng identity sau chuẩn hóa | Test mapping nhiều mã nguồn về cùng identity, reference và hiệu lực crosswalk theo nguồn thực |
| Legacy snapshot | Parent thiếu approval hoặc đóng góp revision cũ có thể bị chặn bảo thủ | Lập kế hoạch xây lại scope/snapshot được duyệt; chưa có workflow chuyển đổi legacy tự động |
| Replay semantics | Manual rerun có run/audit mới; publication identity còn phụ thuộc parent | Owner/QA phân biệt rerun với retry cùng publication request |
| UI nghiệp vụ | Chưa có màn hình mới để vận hành toàn bộ approval workflow | Hiện thao tác bằng API; frontend là phần việc tiếp theo nếu cần |

Ở phiên triển khai trước, Docker CLI không tìm thấy pipe `dockerDesktopLinuxEngine`, nên phần live bị chặn bởi môi trường. Đây là kết quả quan sát tại thời điểm đó, không phải xác nhận lại trạng thái Docker tại lúc đọc báo cáo.

## 10. Cần chuẩn bị gì khi test lại?

### 10.1. Chạy lại local

Chuẩn bị Python 3.11, virtualenv BE và dependencies test/runtime của dự án. Không cần credentials production.

```powershell
Set-Location D:\Caps1\unilake-be
.venv/Scripts/python.exe scripts/run_cleaning_acceptance.py
```

Runner bật UTF-8 để tránh lỗi encoding Windows và xuất evidence vào `docs/testing/cleaning-acceptance/latest/`. Khi chạy trong sandbox Windows, TestClient từng bị kẹt ở socketpair; có thể cần chạy bằng terminal bình thường hoặc quyền chạy ngoài sandbox được cho phép.

### 10.2. Chuẩn bị staging

| Nhóm | Cần chuẩn bị |
|---|---|
| Hạ tầng | PostgreSQL, MinIO, API, ingestion worker hoạt động; kết nối đúng môi trường |
| Schema | Các migration trước đó và migration governance mới; kiểm tra backfill, FK, cột evidence và immutable trigger |
| Cấu hình | `ENVIRONMENT=staging`; xác nhận không rơi về mặc định development |
| Tài khoản | ADMIN, DATA_ADMIN, owner MANAGER của ba domain, STAFF để thử truy cập bị từ chối |
| Quyền storage | Credentials theo service; Bronze versioning; quyền đọc/ghi/xóa đúng thiết kế |
| Policy | Source profiles thực; revision/fingerprint; owner grant; quyết định strict/partial và collapse |
| Dữ liệu | File sạch và biến thể lỗi cho từng nguồn, crosswalk và bảng parent cần thiết; dùng dữ liệu giả hoặc đã xử lý phù hợp |
| Evidence | Job ID, run ID, locator, error code, profile version/fingerprint, checksum/version Bronze, snapshot/manifest và kết quả expected/actual |

### 10.3. Các bước nghiệm thu live

1. Áp dụng migration trên staging có khả năng phục hồi; xác nhận profile cũ chưa tự được approve.
2. Ingest file và chờ trạng thái cuối SUCCESS. Không lấy HTTP 202 hoặc QUEUED làm bằng chứng ingestion thành công.
3. Kiểm tra trước duyệt: file-only/unapproved profile phải bị chặn publication.
4. Giao grant và duyệt bằng đúng owner; thử tự duyệt, sai domain, stale revision/fingerprint và tài khoản không đủ quyền.
5. Chạy các nhóm dữ liệu missing/null/format/type/enum/range/exact/conflicting keys; ghi expected và actual theo profile cụ thể.
6. Chạy strict/partial; thêm trường hợp dòng lỗi sớm trùng khóa với dòng sạch và trường hợp không đủ bằng chứng khóa. Kiểm tra head không đổi khi bị chặn.
7. Thử revoke/edit/deactivate giữa stage và publish; thử reuse/resume/activate sau revoke. Chạy nhiều transaction để kiểm chứng khóa và CAS head.
8. Thử lỗi MinIO đọc/ghi, DB commit và restart; kiểm tra trạng thái run, bằng chứng điều tra, tính bất biến Bronze và khả năng replay.
9. Kiểm thử canonical identity/crosswalk, reference và retained parent rows bằng profile nguồn thực.
10. Tổng hợp kết quả, xử lý gap, lấy owner sign-off và quyết định release theo bằng chứng thực tế.

## 11. Tiêu chí để đóng task ở mức production

Phần code và deliverables local đã có. Để đóng task với kết luận production-ready còn cần đồng thời:

- Migration, trigger, transaction/concurrency và storage permissions có kết quả live được lưu.
- Mọi source/profile dự kiến phát hành có matrix và kết quả nghiệm thu riêng; không chỉ dùng ba baseline entity.
- Owner thực sự được chỉ định, policy được duyệt và approval gắn đúng revision/fingerprint.
- Các trường hợp duplicate qua nhiều bước và canonical identity không còn kết quả công bố thiếu bằng chứng.
- Có cách vận hành quarantine, thu hồi approval, xử lý active snapshot và legacy dữ liệu được thống nhất.

Không lấy 597 PASS làm quyết định tự động mở production. Không biến tài liệu draft thành policy đã được duyệt. Trong quá trình thực hiện chưa cấp grant/approval thật vào môi trường vận hành và chưa deploy.

## 12. Liên kết bằng chứng

- [Matrix chi tiết](D:/Caps1/unilake-be/docs/testing/cleaning-acceptance/latest/matrix.md)
- [Kết quả máy đọc và source hashes](D:/Caps1/unilake-be/docs/testing/cleaning-acceptance/latest/results.json)
- [JUnit của lần chạy cuối](D:/Caps1/unilake-be/docs/testing/cleaning-acceptance/latest/junit.xml)
- [Kết quả tóm tắt](D:/Caps1/unilake-be/docs/testing/cleaning-acceptance/results-summary.md)
- [Gap/bug register](D:/Caps1/unilake-be/docs/testing/cleaning-acceptance/gaps.md)
- [Hướng dẫn production và test lại](D:/Caps1/unilake-be/docs/testing/cleaning-acceptance/production-readiness.md)
- [Evidence mapping version FAIL trước sửa](D:/Caps1/unilake-be/docs/testing/cleaning-acceptance/mapping-version-before.xml)
