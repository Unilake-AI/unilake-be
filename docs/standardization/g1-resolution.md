# G1 — Bản chốt thiết kế triển khai, 15/10/2026

Cập nhật sau S4: engine đối chiếu ID/dictionary, repository crosswalk và migration
đã có code; xem [S4](identity-values-s4.md). Các dòng “việc S4” bên dưới phản ánh
thời điểm chốt G1. G1 approval và activation vẫn không thay đổi.

**Thiết kế kỹ thuật đã chọn; G1 approval vẫn PENDING; activation_allowed=false.**
Yêu cầu triển khai của người dùng là căn cứ hoàn thiện code và chọn phương án kỹ thuật.
Không diễn giải yêu cầu này thành xác nhận sự thật về nguồn hoặc chữ ký của chủ domain.
Schema giữ `0.1.0-draft`: đợt này không sửa field/type/PK/FK hoặc sinh lại schema.

## Các lựa chọn đã chốt cho triển khai

| Quyết định | Chốt cách xử lý | Phạm vi thực thi hiện tại |
|---|---|---|
| D01 | Schema nguồn và Silver độc lập; 22 entity theo S1 | Contract có kiểm thử; không thay schema nguồn |
| D02/D03 | ID nguồn có namespace + entity; mã giữ string/số 0 đầu. Crosswalk cấp canonical BIGINT, không nối người theo tên | S3 giữ pending_s4; cấp ID/crosswalk vẫn là việc S4 |
| D05 | Parquet bất biến + manifest; không triển khai Delta song song | Lựa chọn cho P5, chưa có publisher |
| D06 | Strict snapshot; không công bố khi còn lỗi bắt buộc | Thiết kế publish gate; S2/S3 vẫn silver_ready=false |
| D08 | Binding tường minh, profile có version; config kết nối tách quy tắc | File loader kiểm tra source_system_id trước I/O; S2 kiểm tra key registry |
| D09 | Version nội dung là sha256 của chính bản sao được parse; parser và cấu trúc cột có fingerprint riêng | FileProvenance truyền reader → BronzeBatch → MappingResult → TypeResult |
| S1-B03 | Timestamp có offset đổi UTC; timestamp naive phải có timezone khai báo | S3 thực thi; không mặc định timezone Việt Nam theo máy/chủ dự án |
| S1-B04 | PK không null; UNIQUE chỉ áp tuple không null theo KEY-01 | Quyết định kỹ thuật giữ nullable của schema; owner còn xác nhận diễn đạt luật nguồn |
| S1-B05 | v0.1 chỉ dẫn xuất birth_year theo D04, hoãn GPA/seniority/current position và KPI khác | Giữ thiết kế S1; không đánh dấu KPI/39 luật đã triển khai |
| S1-B06 | Excel locator sheet/dòng vật lý; CSV ordinal record. DB chuẩn hóa phải có export/snapshot bền vững riêng | File handoff có code; PostgreSQL bị từ chối tại entrypoint chuẩn hóa file; raw reader cũ vẫn dùng được |
| Excel | Chặn mọi formula/cache, ô lỗi, cột dữ liệu không có header. Không suy mã số từ format hiển thị | Reader đã thực thi. Ngày native → date32 và float → decimal vẫn bị từ chối an toàn |

## Những việc chưa thể ký thay

| Mục | Phương án cụ thể để review | Cần bằng chứng nào để đóng |
|---|---|---|
| S1-B01 / D04 | Giữ projection PII theo pii-matrix.csv; birth_year trong vùng hạn chế; chưa mở thêm direct identifiers | Người có thẩm quyền xác nhận từng field, mục đích, retention, quyền; quyết định high_school/province/research_field/institution |
| S1-B02 / D07 | Ownership Admissions/Academic/HR giữ theo schema; không đồng nhất ownership với authority nguồn | Danh sách source_system_id thực, authority theo entity/field/khoảng hiệu lực, dictionary/crosswalk và evidence |
| S1-B03 | Không có timezone mặc định | Timezone IANA theo nguồn, hoặc bằng chứng timestamp luôn có offset |
| S1-B04 | Phân loại 39 luật trong g1-rule-disposition.json | Domain owner review công thức điểm, lịch hiệu lực, các luật liên domain và unique-null; tech lead xác nhận stage/severity |
| S1-B05 | Chọn scope v0.1 ở bảng trên | Product/Data Administrator xác nhận phạm vi nghiệm thu và strict snapshot |
| S1-B06 / G0 | Handoff file hoàn thành trong bộ nhớ | DB/MinIO live, versioning/retention, lưu Bronze bất biến; DB export snapshot và dispatch engine rõ ràng nếu cần RDBMS |

Không tự điền tên người duyệt/ngày ký. Ghi xác nhận sau này theo mẫu:
`decision_id, accepted_or_change, scope/version, approver, role, timestamp, evidence`.
Khi thay schema đã duyệt phải tạo version mới; không sửa bản cũ tại chỗ.

## Ý nghĩa phân loại 39 luật

`g1-rule-disposition.json` là **phân loại kỹ thuật cho kế hoạch**, không phải tập rule
đang chạy, không thay severity và không thay business-rule-review.json do builder sinh.

- `normalization_gate`: đánh giá sau typing/identity và trên snapshot cùng các parent
  đã pin, trước publish. Bao gồm uniqueness/FK và consistency không phải KPI.
- `restricted_pre_projection`: cần DOB/tên hoặc dữ liệu hạn chế. Chạy trong vùng hạn
  chế trước projection; không đưa PII trở lại Silver.
- `downstream_policy`: cần công thức/chính sách/lịch hiện hành hoặc ngoại lệ đào tạo.
  Không tính/sửa giá trị trong S3. Trước activation, owner phải quyết định có nâng
  thành publish gate không; không được bỏ qua severity error chỉ vì phân loại downstream.

Luật nhóm 03 đã có một phần kiểm tra kiểu/range trong S3, nhưng enum/identity và toàn
bộ luật chưa đủ; giữ trạng thái nguồn NOT_IMPLEMENTED để không tuyên bố đạt toàn luật.

## Handoff đã triển khai

Entry point mới `read_data_source_batch(...)` nhận catalog data_source_id, dataset_id,
domain_id, batch_id, MappingBinding và optional object_version_id. Nó kiểm tra source
active/namespace, đọc file trong worker, rồi tạo BronzeBatch. API raw `read_data_source`
giữ nguyên; kết quả raw thiếu provenance không được adapter chuẩn hóa nhận.

FileProvenance bất biến gồm source_system_id/data_source_id/dataset_id, source_type,
source_ref gốc, object_version_id, content_sha256, parser_fingerprint,
columns_fingerprint, captured_at UTC, locator_kind và workbook_epoch khi Excel.
Parser fingerprint gồm reader contract `file-v1`, phiên bản thư viện và cấu hình.
Columns fingerprint chỉ chứng minh tên/thứ tự cột, không phải toàn bộ type schema.

Khóa truy vết đầy đủ là `(source_system_id, data_source_id, dataset_id, source_ref,
input_version, parser_fingerprint, locator)`. Không dùng locator riêng làm khóa toàn cục.
S2/S3 giữ nguyên provenance và chặn input_version/namespace không khớp. Đây là
contract giữa các thành phần được tin cậy, không phải chữ ký chống giả mạo caller.

MinIO: nếu caller chọn version thì tải đúng version; nếu không, stat current rồi pin
version có được. Nếu bucket không versioning hoặc version là `null`, checksum vẫn
xác định bytes nhưng **không bảo đảm replay**. Không dùng ETag thay SHA256.
Local: copy vào file tạm riêng, hash và parse bản đó, kiểm tra lại checksum sau đọc.
Nếu nguồn bị ghi đồng thời lúc copy thì checksum chỉ xác định bytes đã capture;
producer vẫn phải atomic-publish file hoàn chỉnh để có snapshot nghiệp vụ nhất quán.

Không có Bronze persistence, quarantine/job API/publisher mới trong đợt này. Thông
tin source_ref có thể nhạy cảm nên provenance thuộc metadata nội bộ có kiểm soát,
không đưa toàn bộ vào log hoặc issue công khai. Replay/idempotency bền vững vẫn thuộc P5.

## Bằng chứng kiểm thử 15/10/2026

382 tests pass: test_file_handoff, test_reader_standardization_integration,
test_excel_reader, test_csv_reader, test_minio_file_resolver, test_source_loader,
test_request_builder, test_reader_factory, test_type_standardization,
test_schema_mapping, test_silver_contracts, test_domains.
Ruff đạt trên các file thay đổi; mypy đạt trên 6 module handoff/S2/S3 được kiểm tra.
Không phải kết quả toàn repository hoặc kiểm thử MinIO/PostgreSQL live.

Ca hồi quy đáng chú ý: thay file gốc sau capture không đổi dữ liệu được parse;
reader sửa bản sao bị phát hiện; CSV multiline dùng ordinal đúng; version bị đổi
ở adapter/S2/S3 bị từ chối; version MinIO lịch sử được truyền đúng xuống client;
thông tin nguồn còn sau cleanup; epoch Excel được giữ; source binding sai bị chặn
trước đọc file. Lỗi công thức Excel không còn bị lỗi cleanup Windows che mất vì
iterator được đóng trước workbook.

Test asyncio chạy ngoài sandbox sau khi chẩn đoán socketpair của event loop Windows
bị kẹt trong sandbox. Catalog/MinIO dùng mock; parser CSV/XLSX đọc file thật.
