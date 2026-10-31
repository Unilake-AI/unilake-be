# Báo cáo hoàn thành task lớn: Làm sạch dữ liệu

| Thuộc tính | Nội dung |
|---|---|
| Ngày báo cáo | 19/10/2026 |
| Phạm vi domain | Admissions, Academic/Training, Human Resources |
| Trạng thái triển khai | Đã có các component Cleaning và API rehearsal cho ba profile demo |
| Trạng thái production | Chưa sẵn sàng: rule/profile cần domain owner phê duyệt và chưa chạy nghiệm thu live |

## 1. Tóm tắt

Đã triển khai cơ chế nhận diện và xử lý missing values, duplicate records,
định dạng không thống nhất và giá trị không hợp lệ trên bản ghi CSV/XLSX đã
được ingestion đọc vào Bronze. Cleaning giữ provenance và locator của bản ghi,
tạo report/issue, rồi chuyển bản ghi đạt qua schema mapping, type/identity
standardization và Silver publication.

Đã bổ sung API thủ công để chạy luồng Bronze → Cleaning → Silver rehearsal cho
các profile tổng hợp của ba domain. API không tự động kích hoạt khi ingestion
hoàn thành, không hỗ trợ toàn bộ 22 entity, và chưa phải luồng production.

**Kết quả xác minh hiện tại:** 159 test liên quan đã pass; Ruff check pass.
Chưa chạy end-to-end với PostgreSQL và MinIO đang hoạt động.

## 2. Luồng xử lý

```text
File CSV/XLSX
  → POST ingestion job
  → ingestion worker đọc file và lưu nguyên object vào Bronze
  → ingestion job SUCCESS, có object URI/version/checksum/size/row count
  → POST /api/v1/pipeline/cleaning/ingestion-jobs/{ingestion_job_id}
  → đọc đúng Bronze object version và xác minh provenance
  → Missing Values
  → Format/Invalid Values
  → Duplicate Records
  → lập CleaningReport; record lỗi được quarantine
  → map source fields → standardize type/identity/crosswalk
  → ghi Parquet và manifest vào Silver rehearsal
  → publish snapshot/head trong PostgreSQL
```

API chỉ nhận job thành công thuộc catalog demo `DEMO_FLOW_*`, file CSV/XLSX
đúng profile phẳng của domain, và người gọi có role `ADMIN` hoặc `DATA_ADMIN`.
API chạy đồng bộ và dùng Silver writer service account riêng.

Nếu có record bị quarantine, API demo trả `QUARANTINED` kèm report và không
publish snapshot. Nếu publish thành công, response `PUBLISHED` có snapshot ID,
scope và thông tin publication. Object có mặt trong MinIO chưa đủ chứng minh
đã publish; phải đối chiếu manifest/snapshot/head trong PostgreSQL.

## 3. Kết quả theo phạm vi và acceptance criteria

| Hạng mục | Kết quả |
|---|---|
| Quy tắc theo domain | Có tài liệu quy tắc baseline cho ba domain; trạng thái vẫn là DRAFT, chờ domain owner phê duyệt |
| Missing values | Phát hiện null/blank theo rule cấu hình; field nullable được giữ null, field bắt buộc bị quarantine; không tự điền giá trị |
| Duplicate records | Nhận diện va chạm PK, unique/composite/business key theo profile; xung đột quarantine cả nhóm, không tự chọn bản ghi thắng hoặc fuzzy-match |
| Format/invalid values | Có rule tường minh cho trim/alias, date, decimal, integer, boolean, pattern, enum, length và range; lỗi invalid quarantine record |
| Cleaning orchestration | Kết hợp theo thứ tự Missing → Format/Invalid → Duplicate; kiểm tra count invariant và giữ provenance/locator |
| Handoff Standardization | Có `CleaningSilverPipeline` chuyển batch đã clean vào mapping, type/identity standardization và Silver publication |
| API/Bronze integration | Có endpoint chạy thủ công cho job Bronze demo; ingestion chưa tự dispatch Cleaning |
| Logging và report | API trả issue/report và log summary gồm actor, job/domain/entity, counts, issue codes và snapshot; log không ghi raw values |
| Kiểm thử | Component, domain handoff, API authorization, MinIO service-account policy/runtime client đã được kiểm tra bằng test |

## 4. Quy tắc xử lý chính

- **Missing:** Chỉ token được khai báo trong profile mới được chuyển thành null.
  Null được giữ nguyên với field nullable; field bắt buộc null/blank bị
  quarantine. Không tự điền trung bình, mode, giá trị mặc định, ngày hiện tại
  hoặc mã định danh.
- **Duplicate:** Dedupe theo khóa đã khai báo trong profile. Xung đột khóa có
  payload khác nhau được quarantine toàn bộ nhóm. Exact duplicate chỉ collapse
  khi profile bật rõ ràng chính sách đó và phải giữ locator các dòng nguồn.
  Không dùng tên/PII để fuzzy-match.
- **Format/invalid:** Chỉ chuẩn hóa theo source format/dictionary đã cấu hình.
  Không đoán locale, timezone, alias hoặc ép kiểu gây mất dữ liệu. Giá trị
  không hợp lệ quarantine toàn record; issue không lưu raw value.
- **Batch/provenance:** Cấu trúc nguồn, mapping binding, object version,
  checksum, column fingerprint, row count và locator phải hợp lệ. Lỗi metadata
  hoặc shape dừng xử lý thay vì tạo kết quả thành công giả.

Chi tiết rule và các quyết định còn chờ domain owner được ghi tại
[cleaning-rules.md](./cleaning-rules.md).

## 5. Kiểm thử và kết quả

Đã chạy tại thư mục `backend/`:

```bash
../.venv/bin/ruff check app/modules/pipeline \
  tests/test_cleaning_domains.py tests/test_cleaning_pipeline.py \
  tests/test_missing_values.py tests/test_duplicate_records.py \
  tests/test_format_validation.py tests/test_manual_cleaning_api.py \
  tests/test_minio_service_accounts.py tests/test_minio_runtime_clients.py

../.venv/bin/python -m pytest \
  tests/test_manual_cleaning_api.py \
  tests/test_minio_service_accounts.py \
  tests/test_minio_runtime_clients.py \
  tests/test_cleaning_domains.py \
  tests/test_cleaning_pipeline.py \
  tests/test_missing_values.py \
  tests/test_duplicate_records.py \
  tests/test_format_validation.py \
  tests/test_file_handoff.py \
  tests/test_reader_standardization_integration.py \
  tests/test_silver_publication.py \
  -q -o addopts=''
```

**Kết quả:** Ruff `All checks passed`; pytest `159 passed in 12.43s`.

Các nhóm test bao phủ:

- Missing, duplicate, format/invalid và count invariant.
- Provenance/shape sai bị chặn, record locator và lineage được giữ.
- Domain fixtures chạy Cleaning → Standardization → Silver rehearsal.
- API cho role admin, từ chối role không đủ quyền, validation catalog demo.
- MinIO runtime client dùng credentials scoped; policy Silver writer không cấp
  quyền ngoài phạm vi cần thiết.
- File handoff và Silver publication contract.

Đây là kết quả automated test. Chưa có lần gọi endpoint với PostgreSQL/MinIO
live để xác nhận provision, network, credentials và object persistence trong
môi trường chạy thực tế. Hướng dẫn chạy thủ công nằm trong
[manual-live-demo.md](../standardization/manual-live-demo.md#chạy-cleaning-qua-swagger).

## 6. Bug, giới hạn và việc cần theo dõi

| Mức | Vấn đề / giới hạn | Ảnh hưởng và hướng xử lý |
|---|---|---|
| Trung bình | Đường chạy API gọi `CleaningPipeline.clean` để lấy report/quarantine; nếu không quarantine, `CleaningSilverPipeline.run` lại chạy Cleaning trên cùng task lần nữa. | Hiện profile là deterministic và test chưa cho thấy sai kết quả, nhưng công việc Cleaning bị lặp và report từ lượt orchestration không được dùng. Nên refactor để chỉ clean một lần, sau đó handoff kết quả đã clean. |
| Trung bình | API chưa gắn `CleaningReportSink` có lưu bền vững. | Client nhận đầy đủ issue qua response và logger ghi summary/count/issue code, nhưng report chi tiết không được lưu vào audit store riêng bởi endpoint. Cần quyết định nơi lưu và retention trước khi dùng production. |
| Cao | Các rule/profile theo source còn DRAFT; nhiều quyết định domain owner vẫn PENDING. | Không bật production cho tới khi phê duyệt null tokens, duplicate winner/collapse, alias, timezone, policy PII và ngưỡng quarantine/publish. |
| Cao | API mới hỗ trợ ba profile demo tổng hợp và chạy đồng bộ; chưa tự động tích hợp ingestion trigger hoặc hỗ trợ đầy đủ 22 entity. | Dùng được để rehearsal/test API; cần profile/mapping được duyệt, xử lý bất đồng bộ và giới hạn tài nguyên trước khi mở rộng. |
| Thấp / ngoài phạm vi Cleaning | PDF/DOCX chưa được trích xuất bởi Cleaning Pipeline. | Cần bước document extraction riêng để chuyển tài liệu thành bản ghi có cấu trúc trước ingestion/cleaning. |
| Chưa xác minh live | Chưa chạy nghiệm thu API → MinIO Bronze read → Silver write → PostgreSQL snapshot/head trong môi trường live. | Cần chạy checklist Swagger/MinIO sau khi có DB migration, demo catalog/crosswalk và service account phù hợp. |

Không có lỗi test nghiêm trọng còn tồn tại trong nhóm test nêu trên. Các mục
trong bảng là giới hạn hoặc rủi ro cần xử lý/đánh giá tiếp, không phải lỗi đã
được test chứng minh gây sai dữ liệu trong rehearsal.

## 7. Kết luận

Task đã có implementation và test tự động cho các cơ chế Cleaning cốt lõi cùng
luồng demo Cleaning → Standardization → Silver rehearsal. Có thể dùng endpoint
thủ công để kiểm thử với ba profile synthetic khi môi trường DB/MinIO và các
prerequisite demo đã sẵn sàng.

Chưa thể kết luận task đủ điều kiện production: rule chưa được domain owner
phê duyệt, chưa có audit persistence cho report chi tiết của API, runner có
đường chạy Cleaning lặp, chưa bao phủ mọi entity/nguồn và chưa có xác minh
live. Các điểm này cần đóng trước khi tuyên bố hoàn thành Definition of Done
cho triển khai production.
