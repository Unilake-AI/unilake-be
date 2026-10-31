# S4 — Chuẩn hóa mã định danh và giá trị

Ngày triển khai: 15/10/2026. Contract Silver vẫn `0.1.0-draft`;
G1 approval PENDING, activation_allowed=false. Các fixture S4 là dữ liệu giả lập.

## Luồng xử lý

1. Ingestion tạo batch có binding, checksum, parser fingerprint và locator.
2. S2 ánh xạ cấu trúc; S3 chuyển kiểu thông thường, giữ ID/FK/enum trong pending_s4.
3. Người quản trị cung cấp đối chiếu nguồn đã có bằng chứng. CrosswalkRepository
   đăng ký ID trong transaction, sau đó freeze một release bất biến có version.
4. IdentityStandardizationService nhận đúng TypeStandardizationService đã tạo S3,
   release crosswalk, dictionary từng field, namespace từng FK và effective_at UTC.
   Cấu hình sai bị từ chối ngay khi khởi tạo; fingerprint/version S3 sai bị chặn khi nhận.
5. S4 resolve ID, chuẩn hóa danh mục, validate giá trị đích; giữ provenance, locator,
   lỗi S2/S3 và phiên bản/fingerprint của toàn bộ cấu hình S4 trong kết quả.
6. P5 còn phải kiểm tra snapshot/PK/FK/unique/39 luật, audit và writer thật. Kết quả
   S4 luôn silver_ready=false; S4 không công bố snapshot hoặc ghi Silver.

## ID, namespace và crosswalk

Khóa tra cứu là `(source_system_id, entity, source_key_tuple)`. Không dùng
data_source_id thay namespace; không nối theo tên hoặc suy cùng người từ cùng mã.
Native int 7 và text "7" dùng cùng key "7"; "007" khác "7"; " 7 " không tự trim.
Tuple được serialize JSON có cấu trúc, tránh nhầm ("1", "23") với ("12", "3").

Canonical ID là BIGINT dương do database cấp, không phải hash. Sequence có thể có
khoảng trống sau rollback/race; retry vẫn giữ cùng ID logic. Cấp mới chỉ xảy ra qua
repository đăng ký, không xảy ra trong preview hoặc khi engine gặp mã chưa biết.

- Register cùng namespace/entity/key + evidence/effective interval trả ID cũ.
- Source identity đã đăng ký không được đổi canonical ID hay evidence/interval tại
  chỗ. Cần một workflow correction riêng trước khi hỗ trợ sửa mapping lịch sử.
- Nhiều source key dùng chung canonical ID chỉ khi caller truyền ID đích và evidence
  tường minh. Repository kiểm tra ID tồn tại và đúng entity. Evidence là tham chiếu
  hồ sơ đối chiếu nội bộ; repository không tự chứng thực người phê duyệt.
- FK namespace phải khai báo từng field, kể cả FK trong cùng hệ thống. Không resolve
  được FK khác null thì REFERENCE_PENDING; không đổi lỗi thành null hợp lệ.
- lecturer dùng cùng canonical employee_id; đăng ký lecturer phải chỉ định employee
  canonical ID đã có. Snapshot và engine chặn shared key không nhất quán.
- employee_qualification giữ cặp ID đã resolve của employee/qualification, không
  cấp một ID đơn mới. FK khác vẫn cần đối chiếu với parent snapshot tại P5.
- Khoảng hiệu lực dùng [valid_from, valid_to); effective_at bắt buộc có offset và
  được pin trong cấu hình, không lấy giờ hiện tại ngầm trong vòng lặp xử lý.

Hai record trong cùng batch ra cùng canonical PK (kể cả PK ghép) đều bị từ chối
CANONICAL_KEY_COLLISION. Không lấy dòng đầu/cuối làm authority. Xung đột giữa các
batch hoặc snapshot khác vẫn cần bước tổng hợp P5 và quyết định D07.

## Dictionary và mã nghiệp vụ

Mỗi enum phải có ValueDictionary được binding tường minh với version. Dictionary
rỗng aliases + allow_canonical=true chỉ nhận các giá trị canonical trong enum đích.
Alias không biết báo UNKNOWN_REFERENCE_CODE. Target sai enum/pattern/length bị chặn.

Chuẩn Unicode NFC; trim và upper/lower/casefold chỉ thực hiện khi profile khai báo.
Alias xung đột sau normalization bị từ chối ngay khi xây cấu hình. Không cho một
alias ghi đè ý nghĩa canonical enum khi bật allow_canonical.

Với code không phải enum, S3 nhận danh sách `value_mapping_fields=("position_code",)`.
Những field này phải là chuỗi thông thường, không phải ID/FK/derived/audit. S3 chỉ
kiểm tra kiểu/null và các tùy chọn chuỗi của nguồn; S4 tra dictionary rồi kiểm tra
pattern/length đích. Đặt allow_canonical=false nếu mọi code phải có trong bảng đối chiếu;
true cho phép code sau trim/case đi qua khi đáp ứng contract đích.

S3 cũng hoãn constraint chiều dài/pattern của enum đến S4 vì alias nguồn có thể dài
hơn code đích. Đây không phải nới lỏng output Silver: chỉ các giá trị sau mapping hợp
lệ mới được nhận. Optional null được giữ khi schema cho phép; không sửa unknown thành null.

Cấu hình engine/dictionary/crosswalk snapshot bất biến trong bộ nhớ. Fingerprint gồm
S3 fingerprint, version S4, nội dung crosswalk, dictionary, FK namespaces và effective_at.
Dictionary hiện do caller cung cấp từ artifact có version; chưa có API quản trị hay
registry database khóa version dictionary toàn hệ thống. P5 phải pin cả fingerprint,
không chỉ nhãn version. Crosswalk release đã có persistence và kiểm tra trùng version.

## Repository và migration

Các bảng mới: app.canonical_identity, app.identity_crosswalk, app.crosswalk_release.
Migration additive ở `infra/postgres/migrations/20261015_s4_crosswalk.sql`, **chưa chạy**.
Schema/models được đăng ký vào Base metadata qua pipeline.models. Không sửa init SQL
để giả định sẽ tự chạy lại trên volume đã khởi tạo; không tạo/drop bảng lúc import.

Sử dụng SQLAlchemy Connection trong transaction do caller quản lý:

```python
with engine.begin() as connection:
    repo = CrosswalkRepository(connection)
    canonical_id = repo.register(
        namespace="SOURCE_ID_FROM_CATALOG", entity="position", source=("007",),
        evidence="REFERENCE_TO_REVIEWED_MATCH", valid_from=effective_at,
    )
    release = repo.freeze("crosswalk-v1")

# Run tiếp theo load đúng release; không freeze lại version cũ sau khi có mapping mới.
with engine.begin() as connection:
    pinned = CrosswalkRepository(connection).load("crosswalk-v1")
```

Với async backend dùng AsyncConnection.run_sync trong transaction; repository không
tự commit. Unique constraint và savepoint bảo vệ race đăng ký cùng key; canonical
allocation của transaction thua được rollback trước khi đọc mapping đã thắng.
READ COMMITTED là giả định cho nhánh retry race PostgreSQL; isolation cao hơn cần
retry cả transaction khi database yêu cầu. Chưa có kiểm thử concurrent PostgreSQL live.

Tables chứa đối chiếu identity và evidence nên thuộc vùng metadata hạn chế. API
ghi crosswalk và phân quyền quản trị chưa được mở trong đợt này. Không nhập dữ liệu
thật cho tới khi D04/D07/G1 được xác nhận. Chưa có cơ chế sửa/xóa/merge lịch sử.

## Chạy preview

Từ repo root:

```powershell
.\.venv\Scripts\python.exe scripts/standardization/preview_s4.py --case admissions-flat
.\.venv\Scripts\python.exe scripts/standardization/preview_s4.py --case academic-flat
.\.venv\Scripts\python.exe scripts/standardization/preview_s4.py --case hr-flat
```

Cũng hỗ trợ ba case `*-nested`. Preview đọc fixture S2 và
`data/standardization/s4/preview-profiles.json`; không kết nối database/cấp ID/ghi storage.
Ví dụ: "Đang diễn ra" → Ongoing, "Nhân viên" → Staff; ID giả lập lần lượt
100007/200008/300009. Không dùng các mapping này làm dữ liệu production.

## Kiểm thử và giới hạn

Test all 22 entities, shared/composite key, FK namespace, alias dài hơn target,
canonical collision, dữ liệu thiếu đối chiếu, hiệu lực, cấu hình bất biến, fingerprint
và bảo toàn upstream issues. Test reader thực CSV/XLSX qua S2/S3/S4 trên cả ba domain;
CLI preview flat/nested; repository dùng SQLite file để kiểm tra commit/reopen,
rollback, retry và version conflict. SQLite là test double cho persistence, không
phải thay lựa chọn PostgreSQL của dự án.

Chưa xác minh migration/locking/concurrency PostgreSQL thật, authority nguồn thật,
parent snapshot membership, unique ngoài PK xuyên batch, 39 luật DQ, audit enrichment
hay Parquet publish. Giới hạn Excel native date/float của S3 vẫn còn nguyên.

Hồi quy ngày 15/10/2026: **440 tests pass**, gồm test_identity_standardization,
test_crosswalk_repository và các suite S1–S3/domain/readers/handoff. Ruff đạt và
mypy đạt trên 5 module S3/S4 được kiểm tra. DDL models compile được cho dialect
PostgreSQL; đó không phải bằng chứng migration đã được áp dụng lên database thật.
