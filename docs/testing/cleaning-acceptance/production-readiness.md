# Cleaning: kiểm soát trước khi đưa lên production

Phạm vi đã mở rộng theo yêu cầu: kiểm thử Cleaning Sprint 4 và bổ sung kiểm soát sử dụng rule trên môi trường vận hành. Không viết lại engine Cleaning, không tự duyệt các schema mẫu. Code hiện tại là căn cứ; draft chỉ để đối chiếu.

## Những gì đã triển khai

| Yêu cầu | Cơ chế |
|---|---|
| Rule chưa duyệt không được công bố | ENVIRONMENT ngoài `development`, `local`, `test` bắt buộc approval. Bao gồm `production`, `staging` và tên môi trường không nhận diện. Không có cờ tắt gate trong production. Preview vẫn dùng được để review. |
| Phiên bản có thể truy lại | Mỗi lần tạo/sửa profile lưu definition và fingerprint vào `silver_profile_revision` cùng giao dịch với current profile. PostgreSQL trigger cấm UPDATE/DELETE revision. Migration chỉ backfill revision hiện có; không phục dựng lịch sử đã mất và không auto-approve. |
| Đúng người duyệt | ADMIN giao quyền theo domain cho tài khoản đang có vai trò MANAGER tương ứng. Người duyệt phải có grant chưa thu hồi, đúng domain, tài khoản active, khác người sửa profile. DATA_ADMIN không mặc nhiên là domain owner. |
| Không dùng approval cũ cho rule mới | Approve yêu cầu revision + fingerprint chính xác. Sửa profile cần duyệt lại. Approval đã thu hồi không được kích hoạt lại; phải tạo revision mới. |
| Partial publication là quyết định rõ ràng | Request approve bắt buộc khai báo `allow_partial_publication`; không mặc định true. Có dòng quarantine mà chưa được duyệt partial thì chặn publication. Strict run vẫn chặn toàn bộ publication dù approval cho phép partial. |
| Chặn đường vòng | Kiểm tra lại ở transaction publish, cả reuse/resume và activation; repository không nhận publish production thiếu hàm authorize. Kiểm tra approval của lineage từng batch, scope và schema fingerprint. Không dùng approval của một mapping cho mapping khác. |
| Tránh race với sửa/thu hồi | Khóa profile, approval, grant theo thứ tự xác định; giữ khóa đến khi transaction cập nhật head kết thúc. Cần kiểm chứng hành vi khóa PostgreSQL thật trước release. |
| Evidence | Cleaning run giữ definition, rule version, fingerprint và approval trong `profile_evidence`; report có approval; manifest có toàn bộ bằng chứng đóng góp vào snapshot. Approval cũng nằm trong publication request hash. |
| Không ngầm chọn winner qua các bước | Production kiểm tra lại các khóa source trên toàn batch gốc bằng null/format rules đã khai báo. Nếu dòng còn lại chia sẻ khóa với dòng quarantine, hoặc khóa không đủ bằng chứng, publication bị BLOCKED với locator/rule version. Manifest partial bắt buộc có bằng chứng kiểm tra PASSED. |
| Không coi dữ liệu test là policy | Test chỉ tạo owner/grant/approval giả trong SQLite tạm. Không ghi profile hoặc approval vào DB vận hành. Profile file và dữ liệu legacy không tự được duyệt. |

Profile API trả `approval_status`. Giá trị mặc định hoặc khi approval không còn hợp lệ là `NEEDS_DOMAIN_OWNER_APPROVAL`. `GET /pipeline/profiles/{id}/revisions` cung cấp definition cũ và lịch sử approval/revoke. API lịch sử cleaning trả `profile_evidence` (nullable cho run cũ).

## API và luồng vận hành

Các đường dẫn dưới đây có prefix `/api/v1` theo cấu hình mặc định. Dùng access token của đúng tài khoản; không lưu token vào tài liệu hoặc test results.

1. DATA_ADMIN tạo/sửa `/pipeline/profiles` bằng source profile thực tế. Kiểm tra data_source/dataset/source_system/domain và nguồn Bronze ACTIVE. File profile cần đăng ký vào DB qua API trước khi chạy production.
2. ADMIN gọi `POST /pipeline/profiles/owners/grants` với `domain`, `user_id`, `reason`. Chỉ giao cho người thực sự được tổ chức chỉ định. Ghi lại `grant_id` trả về.
3. Owner xem `GET /pipeline/profiles/{id}/revisions`, preview và kết quả matrix theo nguồn. Chốt các quyết định trong mục tiếp theo.
4. Owner gọi `POST /pipeline/profiles/{id}/approve` với `expected_revision`, `expected_fingerprint`, `grant_id`, `evidence` và `allow_partial_publication`. Evidence cần trỏ tới biên bản/ticket duyệt thực tế, không chỉ ghi “test xanh”.
5. Chạy cleaning qua API hiện có; kiểm tra cleaning status **và** publication status, không coi HTTP thành công là dữ liệu đã công bố.
6. Thu hồi approval: `POST /pipeline/profiles/{id}/revisions/{revision}/revoke`, body `reason`; owner đã duyệt hoặc ADMIN được thực hiện. Thu hồi delegation: ADMIN gọi `POST /pipeline/profiles/owners/grants/{grant_id}/revoke`.

Thu hồi chặn các thao tác công bố/kích hoạt tiếp theo; không tự xóa dữ liệu lịch sử và không tự đổi active head. Nếu dữ liệu đang active cần rút khỏi sử dụng, phải có quyết định vận hành và chọn snapshot còn đủ điều kiện; không tự chọn winner hoặc rollback dữ liệu.

Snapshot dựa trên parent legacy không có approval bị chặn. Snapshot có nhiều profile chỉ hợp lệ khi mọi bằng chứng còn hợp lệ. Hiện gate bảo thủ: parent dùng revision cũ bị chặn khi current profile đã đổi, kể cả khi định thay toàn bộ entity. Cần xây lại snapshot ở scope mới đã được duyệt; không đổi ENVIRONMENT để vượt gate. Đây là giới hạn vận hành có chủ đích, chưa có workflow duyệt chuyển đổi snapshot legacy.

## Quyết định nghiệp vụ còn CẦN DUYỆT

Với từng source/dataset/entity của admissions, academic, hr, owner phải xác nhận:

- Trường bắt buộc/nullable, null tokens; format, kiểu, locale/timezone và alias cho phép. Không tự impute.
- Enum/range và khóa primary/unique/composite/business. Constraint không có trong source profile không được tự bổ sung từ draft.
- Có cho collapse exact duplicate hay không; trường nào phải bằng nhau. Conflict không được chọn first/last/most-complete winner.
- Strict hay partial; partial hiện merge theo PK và giữ bản ghi parent không được thay. Owner phải xác nhận semantics này cho snapshot/delta của nguồn.
- Engine vẫn chạy missing → format → duplicate. Admission guard production bổ sung kiểm tra khóa source trên toàn batch: `PARTIAL_KEY_CONFLICT_WITH_QUARANTINE` hoặc `PARTIAL_KEY_EVIDENCE_INCOMPLETE` chặn publication, không chọn winner. Dòng clean vẫn được thống kê là clean nhưng chưa được công bố; không đổi lịch sử quarantine thành dữ liệu khác. Owner cần duyệt cách xử lý/remediate nhóm xung đột và kiểm thử thêm canonical identity/crosswalk trên nguồn thực.
- Crosswalk release, effective time, privacy của `source_preview_columns`, thời hạn giữ evidence và xử lý quarantine.

Cơ chế approval kiểm soát ai đã duyệt nội dung nào; không tự chứng minh nội dung đó đúng nghiệp vụ. Giữ strict khi chưa xác nhận semantics partial và các case xung đột qua nhiều bước.

## Chuẩn bị để test lại

### Local, không cần dịch vụ thật

Tại BE, dùng Python 3.11 với dependencies đã cài:

```powershell
.venv/Scripts/python.exe scripts/run_cleaning_acceptance.py
```

Runner xuất `latest/matrix.md`, `latest/results.json`, `latest/junit.xml`, `latest/pytest.log`. JSON chứa source/test/schema/migration hashes. Governance tests nằm trong `tests/test_silver_profile_governance.py`; quản lý profile và snapshot được kiểm tra trong `tests/test_silver_management_api.py`.

### Staging với PostgreSQL và MinIO thật

1. Chuẩn bị DB staging có thể phục hồi, backup metadata và snapshot references; không dùng dữ liệu cá nhân thật cho negative cases.
2. Áp dụng các migration trước đó, rồi `infra/postgres/migrations/20261112_silver_profile_governance.sql` với `ON_ERROR_STOP`. Fresh DB dùng init schema đã cập nhật. Kiểm tra ba bảng mới, cột `cleaning_run.profile_evidence`, trigger immutable và backfill; mọi profile cũ phải chưa được duyệt. Migration chưa được chạy trên PostgreSQL thật trong phiên này.
3. Khởi động PostgreSQL, MinIO, API, ingestion worker; cấu hình credentials từng service đúng quyền. Bronze cần versioning và quyền reader không ghi/xóa; Silver cần writer và khả năng đọc lại đúng object version. Không chia sẻ `.env` hoặc ghi secrets vào báo cáo.
4. Đặt `ENVIRONMENT=staging` cho tất cả process liên quan. Kiểm tra ứng dụng không vô tình dùng mặc định `development`. Chuẩn bị ADMIN, DATA_ADMIN, owner MANAGER từng domain và STAFF để thử RBAC.
5. Chuẩn bị source profiles thật, source files sạch và biến thể lỗi cho **mọi entity sẽ phát hành**. Matrix mẫu ba entity không thay thế acceptance của toàn bộ nguồn. Cần đủ crosswalk và parent tables để FK có thể được kiểm chứng.
6. Ingest và chờ job cuối SUCCESS; lưu job ID, object version, SHA-256, row count. Lưu hash Bronze trước/sau mọi thao tác. Đăng ký và duyệt profile bằng tài khoản phù hợp.
7. Chạy matrix dữ liệu và matrix governance bên dưới. Lưu request IDs, cleaning run ID, approval revision/fingerprint, error codes/locators, manifest/snapshot ID; không chỉ chụp giao diện.

### Matrix live bắt buộc trước phát hành

| Case | Expected | Trạng thái phiên này |
|---|---|---|
| Missing/null, format/type, enum/range, exact/conflicting keys trên source thực từng domain | Đúng source profile; không điền thiếu hoặc tự chọn winner | SKIP — cần profile/dữ liệu nguồn thực đã được duyệt |
| File-only hoặc revision chưa duyệt | Không có Silver publication | SKIP — live |
| Đổi profile sau approve, thu hồi approval/grant, deactivate owner | Publish/reuse/resume/activate bị chặn; head không thay sai | SKIP — live |
| Dòng lỗi sớm trùng khóa source với dòng sạch, hoặc khóa lỗi không thể đối chiếu | Partial bị BLOCKED; report có code/locator/rule version, không có winner | SKIP — live; 30 ca component ba domain đã chạy |
| Strict và partial cùng input có dòng lỗi | Strict không publish; partial chỉ khi policy cho phép, quarantine và retained rows đúng | SKIP — live, cần chốt policy |
| Hai transaction: pause sau stage, revoke/edit trước commit publish | Transaction cuối kiểm tra lại và không công bố bằng chứng cũ | SKIP — PostgreSQL concurrency |
| Publish giữ khóa trước revoke | Revoke đợi transaction; audit thể hiện thứ tự commit, lần publish tiếp theo bị chặn | SKIP — PostgreSQL concurrency |
| Hai profile cùng grant, nhiều publisher song song | Không đảo thứ tự lock; CAS head chỉ cho một kết quả hợp lệ | SKIP — PostgreSQL concurrency |
| MinIO ghi/đọc lại lỗi, DB commit lỗi | Không đổi active head; run có trạng thái/evidence điều tra | SKIP — fault injection |
| Restart API/worker; replay đúng version | Revision/approval/quarantine còn truy được; không nhân đôi snapshot cùng publication request | SKIP — live |
| Bronze và revision bất biến | Bytes/version Bronze giữ nguyên; SQL UPDATE/DELETE revision bị từ chối | SKIP — IAM và PostgreSQL trigger |

Ngày 12/11/2026: Docker CLI không kết nối được `dockerDesktopLinuxEngine` vì engine chưa hoạt động. Local test không chứng minh các case live này. Không gộp SKIP live vào PASS local hoặc tự cho phép mở production.

## Điều kiện kết thúc task và release

Deliverables kỹ thuật gồm matrix, test tự động, kết quả, gap register và các kiểm soát nêu trên. Để ký nghiệm thu production còn cần: hoàn thành matrix nguồn thực, owner approval thực, xác nhận migration/transaction/IAM bằng live evidence và duyệt semantics partial và kiểm chứng guard khóa/canonical identity trên dữ liệu nguồn thực. Không có thao tác deploy hoặc cấp approval thật trong phiên làm việc này.
