# UDL-175 / UDL-176 — Dataset lineage v1

Ngày: 2026-10-31. Phạm vi: contract và tracking Source → Bronze, Bronze → Silver.
Không phải nghiệm thu toàn bộ story UDL-20: API/viewer và Gold thực tế là phần còn lại.

## Căn cứ và đối chiếu

- Task UDL-175 yêu cầu xác định Source/Bronze/Silver/Gold, processing run,
  transformation từ `data_lineage`; UDL-176 yêu cầu tracking ingestion và Bronze →
  Silver, không giả lập Gold. Nội dung task do người dùng cung cấp trong phiên làm việc.
- `infra/postgres/init/02-schema.sql`: `dataset`, `ingestion_job`,
  `transformation_step`, `data_processing_run`, `data_lineage`.
- `docs/standardization/silver-integration-p5.md`: manifest bất biến, publication
  bằng transaction/CAS, parent snapshot, retry/reuse, rehearsal khác production.
- `docs/module-boundary-questions-and-decisions.md`: ingestion chỉ Source → Bronze;
  pipeline xử lý downstream; governance sở hữu contract lineage; qua module dùng DTO.
- `app/bronze_capture.py`: output có uri/version_id/sha256/source_version_id.
- `app/modules/pipeline/silver_publication.py`: bằng chứng batch, schema,
  mapping, conversion, identity, crosswalk, dictionary và entity artifact đã tồn tại.

`data_lineage` hiện là column lineage: bắt buộc source_column_id và target_column_id,
liên kết transformation_step. Không dùng bảng này để tạo cột giả cho ingestion file.
`transformation_step` có source_dataset_id/target_dataset_id nhưng chưa liên kết
data_processing_run. Run chung dùng BIGINT, Silver run dùng UUID; không đồng nhất ID.

## Quyết định triển khai v1

Tận dụng metadata bền vững hiện có, không thêm bảng song song hoặc thay DDL:

| Đối tượng | Định danh / nơi lưu |
|---|---|
| Source asset | `source:<data_source_id>:<source_table>`; không tạo dataset DB giả |
| Bronze asset | `dataset:<dataset_id>`, version là object version thực tế |
| Silver asset | `silver:<scope>:<domain>:<entity>`, version là snapshot UUID |
| Gold asset | Layer được contract hỗ trợ; chưa có producer trong phạm vi này |
| Ingestion run | `ingestion:<ingestion_job_id>`; trạng thái/thời gian lấy từ job |
| Silver run | `silver:<silver_run.run_id>`; trạng thái lấy từ silver_run/snapshot |
| Transformation | Các bước có bằng chứng trong derivation; không giả timestamp riêng |
| Column lineage | Giữ nguyên data_lineage; chưa tự sinh mapping cột từ tên giống nhau |

Đây là contract mức dataset/phiên bản, không phải migration chuẩn hóa toàn bộ các
bảng lineage. Nếu cần index truy vấn graph trong DB ở giai đoạn API, phải thiết kế
projection từ evidence này; không để projection trở thành nguồn trạng thái độc lập.

## Contract máy đọc được

`app/modules/governance/contracts.py` là DTO Pydantic, extra fields bị từ chối.

- `LineageEvidence`: contract_version=`dataset-lineage-v1`, run_ref, mode,
  parent_snapshot, derivations (ít nhất một).
- `DatasetDerivation`: inputs (ít nhất một), output, transformations theo thứ tự.
  Một output có thể có nhiều batch/derivation; không suy ra chúng là nhiều snapshot.
- `AssetReference`: asset_id, layer, domain, version, uri, checksum,
  producer_run_ref. Layer là SOURCE/BRONZE/SILVER/GOLD hoặc null khi chưa xác minh.
- `TransformationEvidence`: name, version và fingerprint nếu upstream cung cấp.

Source version có thể null: không suy ra snapshot DB nguồn từ thời gian enqueue.
Bronze object version và Silver snapshot là hai loại version khác nhau. Không join
graph chỉ bằng checksum hoặc tên bảng: dùng asset/version và producer_run_ref.
URI và manifest là metadata hạn chế; không trả nguyên chúng cho frontend công khai.
Contract không chứa raw rows, thông tin kết nối, signed URL hoặc credentials.

## Tracking Source → Bronze

`IngestionExecutionService.execute` tạo `output.lineage` sau khi writer trả receipt,
trước `IngestionRepository.finish`. Job SUCCESS và output được commit cùng transaction
hiện có. Không phát sự kiện vào danh sách in-memory rồi coi là durable.

- Source lấy data_source_id/source_table của job đã validate; domain lấy dataset.
- Bronze lấy dataset_id và uri/version/checksum của writer.
- Transformation là INGESTION_CAPTURE, không gọi DIRECT_COPY cho mọi loại nguồn:
  PostgreSQL reader có thể xuất Parquet, trong khi file giữ byte gốc.
- Failed job không có output thành công. Lỗi/validation report và thời gian lấy từ
  job hiện có; lease recovery cũng không tạo output lineage.
- Consumer chỉ đọc evidence khi job SUCCESS. Object upload xong nhưng finish chưa
  commit không được coi là published. Không có distributed transaction MinIO/DB.

## Tracking Bronze → Silver

Manifest mới có `dataset_lineage`, tạo từ đúng `updated_lineage` và entity artifacts
đã ghi/readback, trước STAGED và CAS publish. Bằng chứng chỉ có hiệu lực khi repository
xác nhận snapshot published; không đọc trực tiếp một manifest mồ côi rồi coi là thành công.

- Manual runner chỉ gắn ingestion_job_id vào provenance sau khi đối chiếu SUCCESS
  job, object version, size, checksum và row count theo kiểm tra sẵn có.
- Input có liên kết đó được ghi BRONZE và producer_run_ref. Input fixture/file chưa
  đối chiếu job có layer=null; không suy ra Bronze chỉ vì đường dẫn là s3://.
- CleaningPipeline ghi phiên bản CLEAN_MISSING, CLEAN_FORMAT, CLEAN_DUPLICATES vào
  `processing_steps` của batch khi thực sự chạy. Metadata này được truyền qua
  MappingResult → TypeResult → IdentityResult, tách khỏi FileProvenance bất biến.
  Không có cleaning thì không ghi bước này.
- Normalization ghi SCHEMA_MAPPING, TYPE_CONVERSION, IDENTITY_NORMALIZATION từ
  IdentityResult. Chi tiết mapping_id, crosswalk/dictionary, counts và record locator
  vẫn nằm trong lineage batch hiện có của manifest, không nhân bản raw data.
- `dataset_lineage` chỉ mô tả entity được cập nhật. Entity kế thừa truy theo
  parent_snapshot/parent_manifest và entity lineage đã có; không gán nguồn mới cho chúng.
- Retry/reuse dùng snapshot cũ; không sửa manifest cũ hoặc thêm edge vào snapshot đó.
- Resume dùng manifest đã stage và cơ chế publish hiện có, không sinh evidence mới.
- Rehearsal được đánh dấu rõ. Production gate hiện có không bị nới lỏng.

## Khả năng truy vết lỗi và giới hạn

Theo producer_run_ref để đi từ Silver input về ingestion job, sau đó Source.
Đối chiếu Silver manifest batch với version của mapping/schema/conversion/identity.
Lỗi publication xem silver_run.issues; lỗi ingestion xem job.error_message và
validation_report. Không tạo output thành công cho run lỗi.

Lỗi cleaning/normalization xảy ra trước khi gọi publisher chưa có Silver run được
tạo; cơ chế report hiện có không đồng nghĩa đã có journal bền vững cho toàn bộ các
bước thất bại. Muốn quản lý mọi attempt từ đầu đến cuối cần subtask processing-run
lifecycle riêng. Contract này không bịa ra thời gian/số dòng/trạng thái cho từng bước.

Không backfill evidence cho job/snapshot cũ bằng suy đoán. Manifest cũ vẫn đọc được
theo contract P5; thiếu dataset_lineage phải được API sau này thể hiện là legacy/missing.
Tham chiếu ingestion_job_id không tham gia request hash hoặc source_record_id.
Input không có cleaning giữ cách tính hash trước thay đổi lineage; processing_steps
chỉ được thêm vào hash khi có, để đổi phiên bản cleaning không reuse kết quả cũ.
Snapshot đã công bố không bị sửa hoặc backfill. Không chạy migration/reset volume.

## Đọc lineage từ bằng chứng đã công bố

`SilverPublicationService.read_lineage(snapshot_id, max_depth=100)` trả SnapshotLineage:
snapshot_id được yêu cầu, evidence theo run nguồn và missing_entities.

- Mỗi snapshot và parent phải có trong repository published, đúng scope; đối chiếu
  manifest reference đã pin, ID/mode/schema, output version/checksum/domain.
- Chỉ tải manifest, không tải Parquet/rows cho truy vấn metadata này. Publication đã
  thực hiện readback; lineage read không thay thế việc kiểm tra byte dữ liệu khi đọc dataset.
- Entity cập nhật lấy evidence của lần cập nhật; entity kế thừa theo parent với
  cùng object reference. Không nhận nguồn mới cho entity chỉ được kế thừa.
- Entity được ghi lại nhưng thiếu evidence phải nằm trong missing_entities; tuyệt
  đối không dùng lineage cũ của entity ở parent để điền chỗ thiếu.
- Giới hạn độ sâu và phát hiện vòng lặp; không trả graph thiếu mà giả là đầy đủ.
- Đây là service nội bộ để hỗ trợ subtask API; không mở HTTP endpoint hoặc bỏ qua RBAC.

## Nghiệm thu và phần chưa bao gồm

Kiểm tra capture nguồn–đích, đúng version/run, no output khi thất bại, publication
readback, reuse không trùng, parent giữ nguyên và không có GOLD giả.

UDL-177/API/viewer chưa triển khai trong thay đổi này; không mở endpoint thiếu auth.
Không thay thế GovernanceService.lineage in-memory bằng graph endpoint trong hai
subtask này. Consumer API phải kiểm tra lineage.read/scope và đọc qua trạng thái
publication, đồng thời xử lý evidence legacy/missing.

Gold chỉ được ghi khi có publisher thực tế. Vì vậy không tuyên bố AC toàn tuyến
Source → Bronze → Silver → Gold đã hoàn thành.
