# P5 — Tích hợp Silver qua Parquet và manifest

Ngày triển khai: 15/10/2026. Đã có pipeline versioned và luồng rehearsal ghi/đọc
Parquet thật. **G1 chưa approved, production chưa kích hoạt; migration chưa áp dụng
vào PostgreSQL/MinIO thật.** Không thay quyết định Parquet bằng Delta.

## Luồng product

1. Caller lấy batch từ ingestion có version/locator và chọn chính xác S2/S3/S4
   profile. `NormalizationTask` gồm batch, mapper, S4 service (giữ S3 service), cùng
   restricted derivation inputs/version nếu có birth_year.
2. `SilverPipeline.run(tasks, base_snapshot=...)` chạy S2 → S3 → S4 đầy đủ, không
   chạy preview và không gọi transformer Gold của orchestrator legacy.
3. `SilverPublicationService` tạo run ID trước validation/writes, ghi trạng thái
   PREPARING, pin parent snapshot và toàn bộ contract/profile fingerprints.
4. Chặn preview, rejected rows, upstream errors, count sai, schema/provenance sai,
   field ngoài target. Bổ sung audit, kiểm tra kiểu native và target constraints.
5. Snapshot ứng viên gồm toàn bộ bảng trong parent đã published, thay thế các entity
   được truyền vào run bằng dữ liệu mới. Kiểm tra PK, UNIQUE không null, unique-together,
   FK và cycle tự tham chiếu trên toàn bộ ứng viên. Parent không được cập nhật vẫn
   được đọc theo object version/checksum trong manifest đã pin.
6. Kiểm tra governance/DQ gate; ghi Parquet ở prefix run riêng; đọc lại bytes từ storage,
   đối chiếu checksum, Arrow schema, row count và giá trị. Không ép decimal thành float.
7. Ghi immutable manifest chứa đầy đủ entity references và lineage, kiểm tra lại,
   cập nhật STAGED trong metadata.
8. Một transaction metadata insert snapshot, CAS con trỏ active từ đúng parent sang
   snapshot mới, và đổi run sang PUBLISHED. Nếu CAS thất bại, rollback cả transaction.
   Consumer chỉ đọc snapshot được repository xác nhận published.

Các entity cập nhật là **full replacement**, không phải upsert theo từng row. Truyền
hai batch cùng entity nghĩa là ghép hai batch thành toàn bộ dữ liệu thay thế entity đó.
Truyền entity rỗng là yêu cầu xóa sạch nội dung của entity trong snapshot mới; nếu còn
child tham chiếu, FK gate chặn. Snapshot cũ vẫn tồn tại và có thể đọc theo ID.

## Audit và kiểu lưu trữ

- `_source_system_id`, `_batch_id`, `_mapping_version`, `_schema_version` lấy từ handoff.
- `_run_id` là UUID attempt, `_processed_at` là timestamp UTC của run.
- `_source_record_id` là SHA256 của namespace/provenance, entity, input version và
  locator; loại captured_at khỏi khóa. Không dùng tên/ngày sinh hay raw identity làm locator.
- Decimal → decimal128(p,s), bigint → int64, int → int32, date → date32,
  timestamp → timestamp[us, UTC], boolean → bool, text/varchar → string.
- Field có nullable=false được lưu với Arrow nullable=false. Validate không tự cast
  float/string thành kiểu đúng ở P5. Enum, pattern, length, ranges và const vẫn được kiểm tra.
- Audit không được lấy từ business records; field dư (kể cả PII) chặn snapshot.

Manifest giữ key/schema/mapping versions, S3/S4 fingerprints, crosswalk/dictionary
versions, input version, provenance, counts và upstream issues từng batch. Parent
snapshot ID và parent manifest reference được pin. Metadata/manifest là vùng hạn chế;
source_ref có thể nhạy cảm, không đưa nguyên manifest vào API/log công khai.

Trong lineage từng batch có bảng `record_locators`: ánh xạ `_source_record_id` opaque
về locator gốc (sheet/dòng hoặc ordinal). Vì vậy có thể truy ngược dòng Parquet tới
input version và record nguồn mà không phải đưa locator gốc vào bảng analytics.

## Idempotency và sự cố

Logical request hash gồm scope/mode, parent snapshot, contract fingerprint, governance
policy và nội dung/versions/fingerprints của các input. Batch ID và capture timestamp
không làm thay đổi logical request. Cùng request đã published trả snapshot cũ và
`reused=true`; không ghi thêm Parquet và không quay active pointer về snapshot cũ.

Parent là thành phần request: muốn cập nhật từ snapshot active mới phải truyền ID mới.
Caller truyền parent cũ khi đã có run khác published thì nhận ACTIVE_SNAPSHOT_CHANGED.
Đổi contract toàn bộ làm parent không tương thích bị chặn; migration schema cần kế
hoạch riêng, không tự trộn Parquet/schema version khác trong cùng scope.

Lỗi trước publish ghi FAILED cùng issue entity/field/locator/code, không chứa raw value.
Khi metadata hoàn toàn unavailable, có thể không ghi được FAILED; không cố công bố bằng
file marker. Artifact đã ghi mà metadata chưa commit là orphan/staged, consumer bỏ qua.

`resume(run_id)` dành cho run đã có manifest_ref: đọc và kiểm tra lại toàn bộ artifact,
schema, PK/FK, policy fingerprint và governance gate, sau đó thử CAS transaction lại.
Run chưa ghi được manifest_ref phải chạy lại từ input/profile pinned. Retry tạo attempt
UUID mới; release snapshot logic vẫn được unique constraint bảo vệ.

Chưa có garbage collector hoặc tự xóa orphan. Đối chiếu silver_run/manifest_ref và
silver_snapshot trước khi bổ sung quy trình dọn storage; không xóa theo tuổi file ngầm.
Không có distributed transaction giữa MinIO và PostgreSQL.

## Điều kiện công bố production

Mặc định service là `mode="production"` và đóng khi thiếu điều kiện:

1. Contract không còn draft, `x-approval=APPROVED` và approval policy bật
   activation_allowed, có g1_evidence, schema_fingerprint khớp chính xác contract.
2. Handoff có provenance thật; source_ref s3:// cùng object_version_id không null để
   pin input replay. Local checksum một mình chưa chứng minh có Bronze bền vững.
   P5 không tự persist Bronze hoặc sửa retention của bucket.
3. BusinessGate có dq_version khớp policy, cover đủ 39 rule ID nguồn đã review và không
   có issue error. Warning được giữ trong manifest. BusinessGate là port tích hợp;
   **chưa có evaluator production cho toàn bộ 39 luật**. Luật cần PII phải cung cấp
   bằng chứng từ vùng hạn chế qua lineage, không đưa PII trở lại Silver.

Tests dùng policy/gate giả lập để kiểm tra cơ chế chặn, không phê duyệt tài liệu G1.
Rehearsal bỏ qua governance approval và 39-rule gate nhưng vẫn thực thi schema,
PK/unique/FK/read-back/CAS. Scope metadata có prefix `rehearsal:` và object prefix
`silver/rehearsal/`, khác production. Manifest rehearsal luôn activation_allowed=false.

## Repository, storage và migration

- `SilverRepository`: SQLAlchemy sync Engine, transaction riêng cho start/stage/publish.
  Có thể gọi pipeline từ worker hoặc asyncio.to_thread; không chạy blocking I/O trực tiếp
  trên FastAPI event loop. Chưa thêm endpoint/job scheduler vào luồng datasource hiện có.
- Bảng mới: app.silver_run, app.silver_snapshot, app.silver_head. `scope + request_hash`
  là unique snapshot; active pointer có FK đến published snapshot.
- Migration `infra/postgres/migrations/20261015_p5_silver.sql` additive, chạy một lần
  qua quy trình deploy DB của dự án. Không sửa/xóa volume hoặc trông chờ init SQL tự chạy lại.
- `LocalSilverStore`: filesystem phục vụ rehearsal/test, không ghi đè nội dung khác.
  Hỗ trợ extended-length path Windows vì checksum/run paths dài.
- `MinioSilverStore`: nhận client/bucket từ caller, ghi key chứa content SHA256,
  pin version ID trả về khi có, kiểm tra checksum/size khi đọc; luôn đóng response.
  Immutability ở tầng app không thay IAM, object retention hoặc versioning của bucket.
- Hai store không tự tạo bucket, không lấy hoặc ghi credentials trong manifest.

## Chạy lại rehearsal

Từ repo root, dùng một thư mục riêng cho dữ liệu giả lập:

```powershell
.\.venv\Scripts\python.exe scripts/standardization/rehearse_p5.py --output .local/p5-rehearsal
```

Lệnh tạo SQLite metadata và Parquet thật cho admission_method, semester, position từ
fixture synthetic, cùng receipt.json. Chạy lần hai với cùng fixture sẽ trả snapshot ID
cũ và reused=true. Chỉ script rehearsal mới tự create các bảng SQLite; production service
không tự chạy DDL. Không kết nối PostgreSQL/MinIO hoặc đổi snapshot production.

Đường tích hợp ứng dụng:

```python
task = NormalizationTask(batch, mapping_service, identity_service)
receipt = SilverPipeline(publisher).run(
    (task,), base_snapshot=publisher.repository.head(publisher.scope)
)
rows = publisher.read_snapshot(receipt.snapshot_id)
```

Batch phải đầy đủ entity snapshot theo phạm vi update. Với birth_year truyền thêm
derivation_inputs và derivation_input_version trong NormalizationTask.

## Kiểm chứng và giới hạn

Tests P5 dùng file Parquet thật, database SQLite file và MinIO mock. Có all-22-entity
round-trip; CSV/Excel qua toàn S2–S4 → P5; FK parent missing/replacement; unique/cycle;
empty typed table; upstream reject/preview/PII/float; lỗi storage/metadata, resume,
CAS stale run, corruption checksum, retry giữ logical ID và CLI hai lần.

Chưa kiểm thử PostgreSQL/MinIO live, race nhiều process trên PostgreSQL hoặc API/UI
production. Pipeline hiện materialize snapshot trong memory, chưa phải engine streaming
cho dữ liệu lớn. 39 luật nghiệp vụ, G1 approvals, retention/quarantine nguồn và P6 nghiệm
thu end-to-end môi trường thật vẫn cần hoàn thiện. Không tuyên bố toàn bộ story Done.

Kết quả 15/10/2026: **470 tests pass** trong suite P5, S1–S4/domain/readers/handoff;
trong đó 30 tests P5. Ruff đạt và mypy đạt trên 6 module P5. Tests asyncio của loader
chạy ngoài sandbox do socketpair nội bộ Windows bị kẹt trong sandbox; không kết nối
dịch vụ thật. Không phải bằng chứng toàn bộ repository suite xanh.

Run metadata trong đợt này bắt đầu tại P5. Lỗi cấu hình S2/S3/S4 làm pipeline dừng
trước khi tạo IdentityResult vẫn do caller/job runner ghi nhận; chưa có lifecycle job
bao trùm ingestion đến publish hoặc raw quarantine persistence.
