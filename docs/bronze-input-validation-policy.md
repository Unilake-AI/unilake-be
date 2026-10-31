# Validation đầu vào và báo cáo tiếp nhận Bronze — V1

Ngày: 22/10/2026. Mốc code đối chiếu: `af85785`, nhánh `feature/bronze-input-validation`.

Trạng thái: baseline thiết kế để triển khai và kiểm thử. Tài liệu không chứng minh các khả năng đề xuất đã có trong code hoặc đã được phê duyệt production. Không thay đổi hành vi ingestion trong bước lập tài liệu này.

## 1. Mục tiêu và ranh giới

Xác định nguồn CSV, Excel hoặc PostgreSQL có thể được tiếp nhận vào Bronze một cách truy vết được hay không. Tái sử dụng reader, worker và cơ chế xác minh MinIO đang có; không dùng trigger DuckDB để điều phối.

- Bronze lưu nguyên bytes CSV/Excel; bảng PostgreSQL được xuất sang Parquet giữ kiểu dữ liệu.
- Validator không sửa giá trị nghiệp vụ, loại dòng, deduplicate hoặc chuẩn hóa về schema Silver.
- Contract đầu vào mô tả cấu trúc **nguồn**, không yêu cầu tên cột nguồn giống schema Silver.
- Lỗi nghiệp vụ như điểm vượt thang điểm, mã chưa có crosswalk, ngày dạng chuỗi không hợp lệ thuộc Cleaning/Standardization sau Bronze.
- V1 áp dụng toàn bộ hoặc không tiếp nhận: lỗi parse/cấu trúc bắt buộc chặn toàn job. Chưa hỗ trợ PARTIAL hoặc tự bỏ dòng lỗi.
- PASS validation chưa phải SUCCESS ingestion: cần ghi object, xác minh và commit metadata thành công.

## 2. Hiện trạng đã kiểm chứng

| Thành phần | Đã có | Khoảng trống cần triển khai |
|---|---|---|
| execution.py | Quyền, nguồn active, dataset Bronze cùng nguồn, tên file/bảng, chặn job đang chạy | Chưa có policy/report đầu vào thống nhất |
| csv_reader.py | Cấu hình parser, tồn tại file, DuckDB all_varchar | Thiếu contract cột, chính sách header/rỗng rõ ràng; fetchall |
| excel_reader.py | Sheet/header, lỗi/công thức, cột không tên, locator, epoch | Quy tắc cố định; thiếu policy version và report bền vững |
| postgresql_reader.py | Cấu hình, identifier an toàn, READ_ONLY | Chưa kiểm tra contract schema nguồn; fetchall |
| bronze_capture.py | Bản sao file; xuất DB; checksum; metadata/version/readback | Chưa bàn giao báo cáo validation cùng artifact |
| ingestion_repo.py | Job/status/stage/counts/output | rows_rejected hiện đặt 0; chưa triển khai PARTIAL |

Reader có thể biết lỗi cụ thể nhưng execution đang thu gọn thành mã giai đoạn như `INGESTION_READING_FAILED`. Cần giữ mã lỗi an toàn có cấu trúc, không trả nguyên exception từ connector.

## 3. Kết quả validation và trạng thái job

| Kết quả report | Ý nghĩa | Quyết định |
|---|---|---|
| PASS | Toàn bộ kiểm tra bắt buộc hoàn tất, không có warning | Cho phép ghi Bronze |
| WARNING | Kiểm tra bắt buộc hoàn tất, có trường hợp được policy cho phép | Cho phép ghi Bronze; lưu cảnh báo |
| FAIL | Có vi phạm policy | Không ghi/công bố Bronze thành công |
| ERROR | Không thể hoàn tất kiểm tra do hạ tầng hoặc lỗi thực thi | Không được suy ra dữ liệu hợp lệ |

Giữ các trạng thái job hiện có; không thêm enum trạng thái chỉ để phản ánh warning:

- Sai yêu cầu/cấu hình được phát hiện trước enqueue: `REJECTED`, stage `VALIDATION`.
- Job đã nhận, validation thất bại: `FAILED`; report phân biệt FAIL với ERROR.
- Ghi/xác minh/commit thất bại: `FAILED`, kể cả report trước đó PASS.
- Hoàn tất kiểm tra bắt buộc, ghi/xác minh/commit: `SUCCESS`, report có thể PASS hoặc WARNING.
- Đề xuất bổ sung stage `VALIDATING` trong thời gian kiểm tra nội dung, không thay nghĩa của `VALIDATION` trước enqueue.

Không tự retry lỗi policy. Lỗi mạng/hạ tầng có thể retry theo cơ chế được thiết kế riêng; giữ quan hệ attempt và định danh đầu vào. V1 chưa tự động retry. Không dùng checksum để coi mọi lần ingest cùng bytes là trùng nghiệp vụ: một lần refresh có thể có chủ đích.

## 4. Contract policy cần cố định theo job

Policy có `policy_id`, `version`, fingerprint nội dung và binding tới source/dataset/source_type. Nội dung gồm:

| Nhóm | Thuộc tính |
|---|---|
| Parser | delimiter/encoding/header/quote/skip; sheet/header_row; schema/table |
| Cấu trúc nguồn | required_columns, optional_columns, column_order, allow_extra_columns |
| Tiếp nhận | allow_empty_records, allowed_file_types, formula_policy |
| Tài nguyên | max_file_bytes, max_columns, max_records, max_cell_chars, timeout_seconds, memory_limit |
| DB | Tên/kiểu cột nguồn cho phép, chiến lược snapshot nhất quán |

Quy tắc mặc định đề xuất:

- So tên cột phân biệt hoa/thường, không fuzzy-match; alias chỉ được chấp nhận khi khai báo rõ.
- Excel giữ cách trim header hiện có; report phải thể hiện danh sách cột quan sát sau bước này. Header trùng sau trim vẫn lỗi.
- Thứ tự cột không bắt buộc, trừ profile khai báo `column_order=strict`.
- Thiếu required column: FAIL. Cột ngoài danh sách: FAIL mặc định; nếu `allow_extra_columns=true` thì WARNING và giữ nguyên dữ liệu.
- Nguồn không có required column nào vẫn phải khai báo danh sách cột hợp lệ hoặc contract positional rõ ràng; không hiểu policy rỗng là schema bất kỳ.
- Không có bản ghi: FAIL mặc định. `allow_empty_records=true` cho phép schema hợp lệ nhưng 0 dòng và trả WARNING. File 0 byte không có cấu trúc vẫn FAIL.
- Không sao chép mật khẩu/token vào policy snapshot, fingerprint công khai hoặc report. Chỉ lưu tham chiếu cấu hình kết nối được phép truy vết.
- Không cho sửa nội dung một policy version đã được job sử dụng. Cập nhật tạo version mới.
- Đọc cấu hình và pin policy/parser tại thời điểm nhận yêu cầu hợp lệ; worker sử dụng snapshot đó, không đọc lại cấu hình mới rồi gán cho job cũ.

Giới hạn tài nguyên phải được điền trong profile môi trường trước nghiệm thu. Chưa có benchmark để tuyên bố một ngưỡng số dòng/dung lượng là năng lực hệ thống. Không thêm trường cấu hình mà chưa thực thi giới hạn; đặc biệt timeout coroutine không đủ để dừng thread đang đọc.

## 5. Quy tắc chung

| Mã đề xuất | Kiểm tra | Kết quả khi vi phạm |
|---|---|---|
| IN-COM-001 | Binding nguồn/dataset active, đúng tầng và nguồn | REJECTED |
| IN-COM-002 | Policy/parser hợp lệ, version tồn tại | REJECTED nếu phát hiện trước enqueue |
| IN-COM-003 | Nguồn tồn tại, truy cập được | FAIL nếu không tồn tại; ERROR nếu lỗi hạ tầng |
| IN-COM-004 | File đúng loại được cấu hình và parser mở được | FAIL |
| IN-COM-005 | Nguồn không vượt giới hạn profile | FAIL; không báo hoàn tất scan |
| IN-COM-006 | Required columns, extra columns, thứ tự khớp contract | FAIL hoặc WARNING theo policy |
| IN-COM-007 | Có dữ liệu theo policy empty | FAIL hoặc WARNING |
| IN-COM-008 | Đầu vào không bị thay đổi trong capture/validation | FAIL |

Mã trên là mã ổn định của báo cáo đề xuất, không thay ngay các mã API đang có. Khi triển khai cần bảng ánh xạ exception → rule/error code và regression test cho API.

## 6. CSV

| Mã đề xuất | Quyết định V1 |
|---|---|
| IN-CSV-001 | delimiter/quote phải là ký tự hợp lệ; encoding thuộc tập parser hỗ trợ; has_header là bool; skip_rows là int không phải bool, >= 0 |
| IN-CSV-002 | Có header: kiểm tra header gốc trống/trùng trước khi parser tự đặt lại tên; áp dụng contract nguồn |
| IN-CSV-003 | Không có header: bắt buộc positional contract với số lượng và tên cột đã khai báo; không tự suy tên nghiệp vụ |
| IN-CSV-004 | Quét toàn bộ file bằng parser, bao gồm escaped quote và trường chứa newline; lỗi decode/parse chặn cả batch |
| IN-CSV-005 | Dòng không đúng cấu trúc cột bị chặn; không bật bỏ qua lỗi hoặc tự đệm thiếu dữ liệu để đạt PASS |

Không dùng split theo dấu phẩy/xuống dòng để validate CSV. Giữ all_varchar ở bước đọc; null/empty token phải có cấu hình xác định và được ghi trong parser snapshot. Bản bytes lưu Bronze không thay đổi. Đếm bản ghi logic, không đếm số dòng vật lý. Quyết định dòng trắng phải tường minh trong parser policy, không dùng nó để âm thầm bỏ dòng lỗi.

## 7. Excel

| Mã đề xuất | Quyết định V1 |
|---|---|
| IN-XLS-001 | Chỉ .xlsx/.xlsm như reader hiện tại; workbook phải mở được; .xls chưa hỗ trợ |
| IN-XLS-002 | Sheet cấu hình phải tồn tại; chỉ xác nhận phạm vi sheet được đọc, không tuyên bố mọi sheet đã được kiểm tra |
| IN-XLS-003 | header_row/skip_rows là int không phải bool và đúng giới hạn; xác định header sau skip như hiện tại |
| IN-XLS-004 | Header thiếu, rỗng ở giữa hoặc trùng sau trim: FAIL; ô header trống ở cuối có thể bỏ theo reader hiện tại |
| IN-XLS-005 | Công thức/ô lỗi trong vùng đọc: FAIL; không dùng cached value thay công thức |
| IN-XLS-006 | Có dữ liệu ngoài cột có tên: FAIL |
| IN-XLS-007 | Dòng hoàn toàn trống được bỏ khỏi kết quả parse; ghi số dòng bỏ qua và giữ tọa độ dòng vật lý cho dòng còn lại |

Lưu workbook epoch để downstream diễn giải serial date. File .xlsm được đọc như workbook; không thực thi macro và không tuyên bố đây là kết quả quét an toàn macro. Giữ nguyên bytes nguồn khi lưu Bronze.

## 8. PostgreSQL

| Mã đề xuất | Quyết định V1 |
|---|---|
| IN-PG-001 | Host/database/user/password cấu hình hợp lệ; port int không phải bool, 1–65535 |
| IN-PG-002 | Chỉ nhận table hoặc schema.table; không nhận SQL tự do |
| IN-PG-003 | Kết nối và SELECT được bằng user nguồn chỉ đọc; phân loại không có bảng/thiếu quyền/lỗi kết nối bằng mã an toàn |
| IN-PG-004 | So tên và kiểu cột nguồn với contract; thay đổi kiểu ngoài tập cho phép: FAIL |
| IN-PG-005 | Kiểm tra schema, đọc và xuất trên cùng snapshot nhất quán; không count ở một lần đọc rồi xác nhận bằng lần đọc khác |
| IN-PG-006 | Bảng rỗng xử lý theo allow_empty_records, nhưng Parquet vẫn phải giữ schema và kiểu đúng |

READ_ONLY ngăn ghi qua kết nối; riêng điều đó chưa chứng minh tính nhất quán snapshot. Cần kiểm chứng hành vi connector hoặc dùng chiến lược đọc nhất quán tường minh khi triển khai. V1 không CDC, incremental hoặc kiểm tra FK/nghiệp vụ trước Bronze. rows_read là số dòng đã đọc hoàn tất của snapshot, không phải ước lượng thống kê DB.

## 9. Hợp đồng ValidationReport đề xuất

Lưu riêng report đầy đủ trong PostgreSQL; job giữ tham chiếu. Tên bảng dự kiến `app.ingestion_validation_run` và `app.ingestion_validation_issue`, chưa có migration trong bước này.

Report gồm:

- report_id, ingestion_job_id, attempt_id, source_type, source_system_id, data_source_id, dataset_id.
- policy_id/version/fingerprint, parser version và cấu hình không chứa secret.
- result, phase, started_at, finished_at, scan_complete.
- source_ref an toàn; source_object_version khi có; checksum/size của artifact khi đã tính xong.
- columns_observed, schema_fingerprint, records_scanned, blank_rows_skipped và counts theo severity.
- Với DB: mô tả phương thức snapshot/extraction; không giả tạo object_version cho bảng DB.
- Danh sách rule đã chạy và rule chưa chạy/bị bỏ qua cùng lý do.
- Issues: rule_id, error_code, severity, field/column, locator nếu xác định được, message an toàn.

Không lưu raw value, password, connection string hoặc exception nguyên văn. Giới hạn số issue chi tiết nhưng vẫn giữ tổng số phát hiện; ghi `issues_truncated`. Khi dừng sớm, ghi `scan_complete=false`, số đếm chỉ là phần đã kiểm tra; không dùng 0 để thay cho thông tin chưa biết.

Report có thể PASS trong khi job FAILED ở bước ghi Bronze. Lưu bền vững kết quả validation trước khi ghi object; nếu lưu report thất bại thì không tiếp tục upload. Nếu DB mất kết nối khi cần ghi lỗi, job sẽ được cơ chế lease recovery xử lý; phải có log vận hành an toàn, không hứa mọi lỗi đều lập tức xuất hiện trong DB.

Nếu upload xong rồi xác minh/commit thất bại, object có thể vẫn tồn tại. Không tự xóa version bằng worker; ghi nhận/reconcile orphan theo metadata. Sự tồn tại object không phải bằng chứng job thành công.

## 10. Vị trí tích hợp

```text
API / start
  → validate catalog và pin policy/parser config
  → QUEUED
Worker claim + heartbeat
  → capture phiên bản nguồn
  → validate trên chính artifact/snapshot được capture
  → lưu report
      FAIL/ERROR → FAILED; không ghi Bronze mới
      PASS/WARNING → WRITING
  → upload đúng artifact → xác minh metadata/checksum/version
  → commit SUCCESS và output tham chiếu report
```

Tái sử dụng reader, không thêm lần parse toàn file thứ hai chỉ để validation. Bổ sung validation trong quá trình đọc/capture, trả cả artifact và report. Source MinIO cần pin version, local cần bản sao riêng; không đọc lại đường dẫn mutable sau PASS để upload.

Các vị trí dự kiến sửa khi triển khai: `modules/ingestion/readers`, `contracts.py`, `execution.py`, `bronze_capture.py`, `repositories/ingestion_repo.py`, model/schema API và migration. Không chuyển Cleaning S2–S4 lên trước Bronze.

Chưa tạo persistent landing/quarantine trong V1. Job bị chặn vẫn có report, nhưng file local tạm có thể bị dọn. Nếu yêu cầu giữ mọi file bị từ chối, bổ sung thiết kế landing/quarantine với retention và quyền truy cập; không tuyên bố đã có chỉ vì có trạng thái FAILED.

## 11. Ma trận nghiệm thu

| Ca kiểm thử | Kỳ vọng |
|---|---|
| CSV hợp lệ có tiếng Việt, quote, newline trong trường | PASS; số bản ghi logic đúng; bytes Bronze khớp |
| CSV sai encoding/cột/quote | FAIL có mã an toàn; không SUCCESS, không bỏ lỗi để tiếp nhận |
| CSV header trống/trùng, hoặc positional contract thiếu | FAIL, không chấp nhận tên tự sinh như schema đã duyệt |
| File không tồn tại hoặc không có quyền | Phân loại lỗi, lưu report hoặc rejection phù hợp giai đoạn |
| CSV/Excel chỉ header hoặc DB 0 dòng | Theo allow_empty_records; schema được giữ |
| Excel thiếu sheet/header trùng/công thức/ô lỗi | FAIL có sheet/row/cell khi xác định được |
| Excel dòng trắng xen kẽ | Dòng hợp lệ giữ locator gốc; số dòng trắng được ghi nhận |
| DB sai credential/bảng/permission | Mã lỗi riêng, không lộ secret |
| DB schema đổi giữa các lần ingest | Kiểm tra theo policy snapshot của từng job |
| Đổi cấu hình catalog sau enqueue | Job cũ vẫn dùng snapshot cấu hình đã pin |
| Sửa file nguồn trong quá trình chạy | Bronze đúng bản capture đã validate hoặc job bị chặn |
| Vượt giới hạn/thất bại giữa scan | FAIL/ERROR; scan_complete=false; không báo số đếm đầy đủ |
| Metadata/checksum sau upload không khớp | Job FAILED dù report đầu vào PASS |
| Lỗi nghiệp vụ trên dữ liệu parse được | Bronze tiếp nhận; Cleaning/Silver xử lý sau |
| Chạy lại cùng đầu vào | Mỗi attempt truy vết được, không ghi đè/xóa phiên bản cũ |

## 12. Thứ tự triển khai

1. Định nghĩa policy/report/issue và validation lỗi cấu hình, không cần dữ liệu lớn.
2. Tích hợp reader CSV/Excel/PostgreSQL, giữ bytes và hành vi chuyển đổi nghiệp vụ.
3. Lưu policy snapshot/report bền vững, nối API/job; kiểm thử việc DB/report thất bại.
4. Kiểm thử MinIO/PostgreSQL thật với dữ liệu nhỏ, bao phủ lỗi có chủ đích.
5. Thay cách materialize toàn bộ dữ liệu bằng batch/stream phù hợp; thực thi giới hạn tài nguyên.
6. Chạy bộ dữ liệu lớn tăng dần; chốt ngưỡng theo đo đạc, không dựa vào số dòng tùy ý.

Điều kiện nghiệm thu: không có đường ingestion production bỏ qua kiểm tra bắt buộc; có report truy vết được; lỗi không tạo SUCCESS giả; không mất bytes nguồn; kiểm thử cả ba loại nguồn. Các rule nghiệp vụ và phê duyệt schema Silver nằm ngoài nghiệm thu đầu vào Bronze này.
# Tiến độ triển khai contract (2026-10-22)

**Cập nhật sau bước contract:** đã nối runtime opt-in theo từng job; xem
[hướng dẫn runtime](bronze-validation-runtime.md). Các dòng "chưa tích hợp runtime" bên dưới
mô tả mốc contract ban đầu, không phải trạng thái mới nhất. Report hiện lưu JSONB trong job;
registry, giới hạn tài nguyên cứng và nghiệm thu live còn chưa hoàn tất.

Đã bổ sung `backend/app/modules/ingestion/validation.py` và test tại
`backend/tests/test_bronze_validation_contracts.py`:

- Policy bất biến, cấu hình parser CSV/Excel/PostgreSQL, source-column contract và giới hạn tài nguyên bắt buộc.
- Fingerprint SHA-256 bao gồm binding nguồn/dataset, parser, quy tắc và giới hạn.
- Danh mục mã lỗi, tọa độ lỗi và tổng số lỗi/cảnh báo; không có trường chứa giá trị dữ liệu hoặc exception nguyên văn.
- Báo cáo tự suy ra PASS/WARNING/FAIL/ERROR; quét chưa hoàn tất hoặc required check chưa chạy không được tiếp nhận.
- JSON phục vụ API có các trường kết quả tính toán. Khi lưu để đọc lại bằng `ValidationReport.model_validate_json`, dùng `model_dump_json(round_trip=True)`; kết quả sẽ được tính lại, không nhận trạng thái do bên gọi gán.

**Chưa tích hợp runtime:** contract chưa thực thi giới hạn tài nguyên, chưa chạy rule trên dữ liệu,
chưa xác minh danh sách rule đầy đủ theo policy, chưa pin policy vào job và chưa lưu report vào PostgreSQL/API.
Worker và đường ghi Bronze hiện tại chưa thay đổi. Bước tiếp theo là xây dựng validator cho reader,
xác định đủ required checks cho từng loại nguồn và nối quyết định tiếp nhận vào worker trước khi ghi Bronze.

Kiểm chứng bước contract: 26 test mới cùng 45 test reader/request builder/MinIO resolver/Bronze metadata đều pass;
Ruff và Mypy cho module mới không báo lỗi. Đây chưa phải kiểm thử tích hợp với PostgreSQL/MinIO thật.

