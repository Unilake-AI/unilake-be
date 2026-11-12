# Kiểm thử chất lượng Cleaning theo mã nguồn

Chạy dịch vụ thật không cần FE: [Hướng dẫn BE live và bộ file cần chuẩn bị](D:/Caps1/unilake-be/docs/testing/cleaning-acceptance/live-backend-guide.md).

Báo cáo đầy đủ về yêu cầu, thay đổi code, bằng chứng và điều kiện nghiệm thu: [Báo cáo xử lý task](D:/Caps1/unilake-be/docs/testing/cleaning-acceptance/task-implementation-report.md).

Ngày lập: 12/11/2026. Phạm vi gồm acceptance/regression cho Cleaning Sprint 4 và bổ sung kiểm soát phê duyệt, lịch sử rule, publication để chuẩn bị production. Không triển khai lại engine Cleaning. Xem [production readiness và hướng dẫn test lại](production-readiness.md).

## 1. Căn cứ và cách hiểu yêu cầu

Expected behavior lấy từ implementation và cấu hình fixture được khai báo tường minh. `docs/data-quality/cleaning-rules.md` là draft; `docs/standardization/source-silver-flow.md` có nội dung cũ. Cả hai chỉ dùng để đối chiếu, không quyết định policy production.

| Yêu cầu | Cách kiểm chứng | Giới hạn kết luận |
|---|---|---|
| Matrix ba domain, input/expected/actual/PASS/FAIL/SKIP | `test_cleaning_acceptance.py`; runner xuất `matrix.md`, `results.json`, JUnit | Không tuyên bố phủ mọi entity/source production |
| Missing/null | Null required bị quarantine; nullable giữ null; `N/A` chỉ thành null khi profile khai báo | CSV ô trống được parser đưa về giá trị thiếu; không đồng nhất với thiếu cột |
| Format/type, enum/range | Sai integer/date/enum; alias tường minh; kiểm tra đúng và ngoài min/max | Không tự áp thêm range/enum không có trong schema |
| Không điền thiếu, không chọn winner tùy tiện | Assert null/output; exact mặc định quarantine; conflicting PK/business key loại cả nhóm; đảo thứ tự dòng | Collapse chỉ được thử như một tùy chọn TEST, không phải đã được owner duyệt |
| Batch reject vs row quarantine | Sai binding/header/checksum/version/row count dừng; batch trộn dòng tốt/xấu giữ số liệu từng loại | Cleaning hoàn tất không đồng nghĩa Silver đã publish |
| Bronze giữ nguyên | Parser CSV/XLSX thật qua `_read_bronze`; so sánh toàn bộ bytes trước/sau kể cả nhánh lỗi | MinIO được thay bằng adapter local; IAM/versioning server cần live test |
| Truy vết | Assert input version, object version, SHA-256, locator, rule version; replay; đổi profile version | Run ID/thời điểm có thể khác giữa hai lần chạy |
| Rule cần duyệt | Mọi case/fixture được gắn `NEEDS_DOMAIN_OWNER_APPROVAL` | PASS kỹ thuật không thay thế phê duyệt |

## 2. Phạm vi test

### Matrix mới

Ba `SourceProfile` tổng hợp: Admissions `admission_method`, Academic `course`, HR `position`. Mỗi profile ánh xạ tên nguồn `src_<field>` sang cột đích, có version/fingerprint; chạy CSV và XLSX bằng reader thật. Mock chỉ nằm ở truy cập object storage. Profile không được ghi vào DB hoặc `config/silver/profiles.json`.

24 tình huống cho mỗi domain/format: valid, required null, nullable null, token chưa/có cấu hình, invalid type, enum lạ/alias, đúng min/dưới min và đúng max/trên max, exact duplicate mặc định/bật collapse, PK conflict, unique business-key conflict, thiếu cột, checksum/version/row count/binding sai, thay rule version, mixed rows, không fuzzy merge. Tên case trong code là nguồn định danh chính xác.

Supplement component-level bổ sung date/null/enum/range cho `program`, `admission_year`, `curriculum`, `semester`, `assignment`, `employee_qualification`; khóa ghép cho `admission`, `curriculum_course`, `employee_qualification`. Các ca này dùng schema/profile component và BronzeBatch synthetic đã có, không tự nhận là source-profile E2E.

Bổ sung 30 ca component kiểm tra khóa qua các bước cho ba domain: lỗi non-key missing/format, biểu diễn khóa khác, khóa thiếu bằng chứng, khóa phân biệt và đảo thứ tự dòng. Đây là guard admission production, không thay engine Cleaning.

24 SKIP dự kiến trong matrix baseline là constraint không tồn tại ở entity đang chọn (ví dụ `course` không có nullable; `position` không khai báo numeric min/max). Ca bổ sung kiểm tra các field đó ở entity thích hợp; không tự bịa policy để biến SKIP thành PASS.

### Test existing được tái sử dụng

- `test_source_cleaning.py`: preview không ghi; strict/partial publication; tất cả dòng lỗi bị chặn; S2/S3/S4 quarantine; CSV/XLSX/XLSM; source binding; profile fingerprint.
- `test_missing_values.py`, `test_format_validation.py`, `test_duplicate_records.py`: token/boolean/decimal/timezone, metadata, khóa nullable/composite, no raw values in issues.
- `test_cleaning_pipeline.py`, `test_cleaning_domains.py`: counts, handoff, provenance, publication theo domain.
- `test_schema_mapping.py`, `test_type_standardization.py`, `test_identity_standardization.py`: mapping/type/crosswalk và canonical key collision.
- `test_quarantine_records.py`: persistence payload, vị trí gốc, nhóm issue, rule version, đọc đúng Bronze; test mới cho version của lỗi S2.
- `test_silver_publication.py`, `test_lineage_contract.py`, file handoff và reader integration: snapshot/manifest/idempotency/lineage.

Tái chạy có kiểm chứng và lập bảng bằng chứng là phần mới của task; không ghi nhận lại việc xây dựng component Sprint 4 như một chức năng mới.

## 3. Behavior hiện tại cần dùng khi test

1. Missing → Format → Duplicate, sau đó S2 mapping → S3 type → S4 identity/dictionary. Duplicate component chỉ nhìn tập dòng đã qua các bước trước.
2. Exact duplicate cùng PK chỉ collapse khi `collapse_exact_duplicates=true`; mặc định quarantine. Payload xung đột hoặc vi phạm unique key không được chọn first/last.
3. `allow_partial_publication=false`: lưu quarantine, publication BLOCKED nếu còn dòng lỗi. `true` (mặc định hiện tại): có thể PUBLISHED_WITH_QUARANTINE khi còn dòng sạch và snapshot đạt kiểm tra. Không còn dòng sạch thì không publish.
4. Partial publication giữ các dòng cũ không trùng PK với dòng mới sạch; publication toàn phần thay bảng được cập nhật. Liên kết snapshot vẫn có thể làm publication thất bại.
5. Count cleaning: `read = passed + quarantined + collapsed`. Kết quả manual cleaning còn tính các dòng bị loại ở standardization. `records_published` là số dòng mới được publish, không nhất thiết bằng tổng số dòng bảng sau partial merge.
6. Profile version kết hợp content fingerprint; cùng nhãn version nhưng thay nội dung vẫn thay rule version. Kiểm tra provenance/parser và output thực tế, không chỉ HTTP 200.
7. Replay cleaning phải xác định về dữ liệu. Mỗi manual cleaning invocation có thể tạo cleaning run/quarantine record mới; không áp yêu cầu “không tạo thêm audit row” của draft lên hiện trạng. Publication reuse chỉ đúng khi request hash, gồm parent snapshot, giống nhau.

## 4. Chạy lại local: chuẩn bị và lệnh

Yêu cầu Windows PowerShell, Python 3.11+, virtualenv của BE đã cài dependency runtime/dev (`pytest`, SQLAlchemy, DuckDB, PyArrow, openpyxl...). Chạy tại repository root; các file schema/contracts và fixture trong `data/standardization` phải có đủ. Không cần khởi động API, PostgreSQL hoặc MinIO cho suite này; không cần mật khẩu production. Không bật live-test flags để thay thế cho suite cô lập.

```powershell
# Nếu chưa có môi trường: tạo venv riêng, rồi cài dependency của repository.
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"

# Chạy acceptance và regression; tự xuất bằng chứng.
.venv/Scripts/python.exe scripts/run_cleaning_acceptance.py --output docs/testing/cleaning-acceptance/latest

# Chỉ chạy matrix mới để debug nhanh.
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_cleaning_acceptance.py -o addopts= -q
```

Nếu đã có `.venv`, không cần tạo/cài lại. Runner chỉ ghi vào thư mục output, thay bộ báo cáo `latest`; dùng `--output` khác để giữ lịch sử. `-o addopts=` chỉ bỏ coverage toàn backend cho suite hẹp; không thay cấu hình CI. Runner trả exit code khác 0 nếu test lỗi, ghi rõ SKIP và không tái dùng XML cũ khi lần chạy bị gián đoạn.

Runner bật UTF-8 cho Python và subprocess vì một số test existing đọc tài liệu tiếng Việt bằng encoding mặc định. Nếu chạy trong sandbox Windows, TestClient/asyncio có thể bị kẹt ở socketpair local: dùng terminal bình thường hoặc quyền chạy ngoài sandbox được cho phép; không đổi test thành PASS/SKIP để che việc treo. Test API trong suite sử dụng app in-process và dependency override, không gọi API production.

Đọc `results.json` để xem input synthetic, expected, actual report, profile fingerprint, rule version, base commit và SHA-256 source/test/schema. Đọc `pytest.log` để xem failure/skip; `junit.xml` dùng cho CI. Đường dẫn/thời điểm trong báo cáo là môi trường đã chạy, không phải yêu cầu cố định cho máy khác.

## 5. Nếu cần xác nhận live trước nghiệm thu production

Chuẩn bị riêng, không dùng kết quả local để tuyên bố các mục sau đã PASS:

- PostgreSQL/MinIO môi trường test cô lập; áp dụng DDL và migration phù hợp (ingestion, validation/upload/reliability, crosswalk, Silver, cleaning runs/quarantine và Silver management). Alembic hiện chưa thay thế bộ SQL migration.
- Bucket Bronze có versioning; tài khoản reader/writer/verifier, Silver writer và upload accounts đúng quyền. Không dùng root thay service account và không ghi credentials vào báo cáo.
- Dataset/source ACTIVE thuộc đúng domain, source type, filename và source system. Mỗi source phải có profile hợp lệ và binding chính xác; file cấu hình mặc định `{}` không đủ.
- Crosswalk release có evidence, namespace, canonical ID và khoảng hiệu lực đúng. Chuẩn bị các bảng tham chiếu/snapshot cần thiết để phân biệt lỗi FK với lỗi cleaning của case.
- Tài khoản DATA_ADMIN/ADMIN cho cleaning. Fixture chỉ chứa dữ liệu tổng hợp; lưu SHA-256 file trước upload, Bronze job SUCCESS, object version/URI, parser options và rows_read.
- Chạy mỗi domain với file valid, mixed và all-invalid; lặp lại partial false/true. Chụp active snapshot trước/sau; đọc report/quarantine/manifest/Parquet. Với failure phải xác minh active snapshot không đổi và Bronze bytes/version không bị sửa.
- Với replay, ghi rõ parent snapshot truyền vào; dùng cùng parent để kiểm tra request reuse. Với policy/version đổi, ghi cả fingerprint trước/sau.
- Với source field nhạy cảm, không đưa raw values vào log; chỉ dùng allowlist preview đã cấu hình. Owner cần xác nhận null tokens, alias/locale/timezone, khóa, enum/range, collapse và partial merge trước production.

Thiếu điều kiện nào thì ghi SKIP cùng điều kiện thiếu, không dùng số liệu kiểm thử lịch sử để điền PASS.

## 6. Approval register

Chưa có bằng chứng owner phê duyệt được cung cấp cho các fixture này. Mọi nhóm dưới đây ở cả ba domain đều **CẦN DUYỆT**:

| Nhóm quyết định | Cần xác nhận |
|---|---|
| Missing/null | Nullable, token theo field; có cho trim khi nhận token không |
| Format/type | Locale, timezone, format ngày, decimal scale, boolean tokens, alias |
| Enum/range | Tập giá trị, cận và thời gian hiệu lực |
| Duplicate | PK/business/composite key, namespace; chính sách exact collapse |
| Publish | Strict/partial; việc giữ dòng cũ khi bản mới có lỗi; snapshot constraints |
| Truy vết | Version profile/schema/crosswalk; bằng chứng duyệt và người duyệt |

Approval phải gắn với profile ID, revision, fingerprint và rule cụ thể. Boolean bật tính năng hoặc test PASS không phải approval evidence. Đã bổ sung gate production và lifecycle duyệt trong code; chưa cấp phê duyệt thật. Xem `production-readiness.md` để đăng ký profile, giao quyền owner, duyệt, thu hồi và chạy live acceptance.
