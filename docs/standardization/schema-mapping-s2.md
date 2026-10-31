# S2 — Schema Mapping

## Đã triển khai

`backend/app/modules/pipeline/schema_mapping.py` cung cấp MappingKey, FieldMapping, MappingProfile, MappingRegistry và SchemaMappingService. `mapping_profiles.py` cung cấp build_profile để khai báo profile từ contract S1. Không có phụ thuộc DB/MinIO hoặc HTTP endpoint; endpoint và persistence thuộc P5.

Registry mới chọn đúng tuple `(source_system_id, domain_id, entity, source_schema_version, mapping_id, mapping_version, target_schema_version)`. Không fallback version mới nhất hoặc source_type. Có thể đăng ký nhiều layout/version cùng CSV. Profile dùng dataclass frozen và tuple; cùng key không được đăng ký lại. Registry này là cấu hình trong bộ nhớ, cần được khởi tạo từ cấu hình có kiểm soát khi startup, chưa phải kho profile DB.

SourceMapping/domain registry cũ vẫn phục vụ demo. Không đổi ngầm hành vi demo; batch có mapping_binding mới bị PipelineOrchestrator.run chặn trước publish để tránh coi dữ liệu chỉ đổi tên là Silver hợp lệ. Entry point S2 là SchemaMappingService.map_batch hoặc preview. S3/S4 sẽ tiêu thụ MappingResult rồi P5 mới nối publisher.

## Handoff từ ingestion

BronzeBatch bổ sung:

- mapping_binding: namespace hệ thống, entity, source schema version, mapping ID/version và target schema version. domain_id/source_type dùng trường hiện có.
- record_locators: một locator thật duy nhất cho từng row, ví dụ object version + dòng CSV hoặc sheet + physical row. Không suy index này sau khi Excel reader bỏ dòng trống.
- source_paths: cấu trúc nguồn do reader/adapter khai báo, kể cả batch rỗng; dùng JSON Pointer.
- metadata.input_version: định danh snapshot/object version hoặc content hash bất biến. S2 yêu cầu hiện diện và ghi lại trong result; ingestion phải chứng minh tính bất biến, S2 không tự truy cập storage xác minh.

Legacy BronzeBatch không bị bắt buộc các trường mới; gọi S2 thiếu binding/version/locators/shape sẽ lỗi cấu hình. Loader hiện tại chưa tự tạo handoff này; ingestion adapter vẫn là dependency G0/B08.

## Phép ánh xạ

| Phép | Hành vi |
|---|---|
| copy | Đổi tên hoặc đọc JSON Pointer, copy sâu giá trị, không chuyển kiểu |
| coalesce | Một hoặc nhiều alias; mặc định các alias hiện diện phải cùng kiểu và cùng giá trị, khác thì MAPPING_CONFLICT |
| first_present | Chính sách tường minh cho coalesce: chọn alias hiện diện đầu tiên theo thứ tự khai báo. Null vẫn là hiện diện |
| concat | Ghép các giá trị string bằng separator khai báo; không tự str hóa số/null |
| split | Tách string bằng separator và index không âm; thiếu phần tử báo lỗi |

JSON Pointer hỗ trợ `/payload/code`, escape `/a~1b/~0key`, index `/items/0/id`. Không wildcard, eval, SQL hoặc explode mảng ngầm. Muốn explode phải chốt mức hạt và khóa cha trước, ngoài contract hiện tại.

`target_fields` lấy từ contract S1. Mỗi target phải có đúng một producer: field mapping hoặc deferred_fields. build_profile không tự đọc repo hoặc xác minh schema trên mạng; caller phải truyền contract/version đã review, không nhận profile tùy ý từ người dùng không có quyền.

Audit và birth_year được khai báo deferred; S2 không điền null giả. PK/FK lúc này vẫn là giá trị nguồn dù field mang tên target: S4 phải resolve canonical ID. Profile bỏ direct PII/free-text khỏi output và preview. Riêng DOB dùng để dẫn xuất birth_year phải được S3 đọc trong vùng hạn chế từ Bronze bất biến theo locator trước khi PII projection hoàn tất; không thể chỉ lấy MappingResult rồi tự suy birth_year khi DOB đã bị loại. Không xóa Bronze sau mapping.

## Lỗi và preview

- Cấu hình thiếu target, trùng target, mapping path sai, phép biến đổi không hỗ trợ, hoặc nguồn khai báo thiếu field bắt buộc: MappingConfigurationError trước xử lý row; không trả kết quả thành công giả.
- Row thiếu field hoặc alias mâu thuẫn: đưa locator vào rejected_locators, không trả partial mapped row dưới accepted.
- Cột ngoài mapping: UNMAPPED_SOURCE_FIELD warning, giữ nguyên input và không sao chép vào target.
- ignored_sources: chủ động bỏ field, không coi đó là drift. Issue chỉ ghi code, severity, locator, field; không raw values.
- preview trả before chỉ gồm paths thực sự mapped, after tại accepted.values; không trả các field ignored. Preview của row lỗi chỉ trả issue/locator, không phơi raw record. Người tích hợp UI phải bảo vệ quyền xem field được giữ như student_code.
- preview_limit chỉ giới hạn số row; inspected_count/total_count phân biệt sample và toàn batch. Preview thành công không bảo đảm các row chưa đọc hợp lệ.
- silver_ready luôn false; một accepted row chỉ đạt mapping cấu trúc, chưa đạt contract kiểu/enum/identity/PII cuối cùng. Strict snapshot publish policy được thực thi ở P5, S2 không publish.

## Chạy thử

Tại repo root:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/standardization/preview_s2.py --case admissions-flat
.\.venv\Scripts\python.exe -X utf8 scripts/standardization/preview_s2.py --case academic-nested
.\.venv\Scripts\python.exe -X utf8 scripts/standardization/preview_s2.py --case hr-nested --limit 1
```

Sáu case tại `data/standardization/s2/examples.json` đều là giả lập, expected outputs viết độc lập. Không cấu hình nguồn production hoặc giả vờ dictionary nghiệp vụ đã được duyệt. Kết quả cho phép nhìn thấy trước/sau; không có thao tác storage hoặc publisher.

## Kiểm thử và giới hạn

182 tests pass khi chạy `tests/test_schema_mapping.py tests/test_silver_contracts.py tests/test_domains.py -o addopts='' -q` từ backend. S2 gồm 30 tests; thêm coverage 44 entity-layout được lặp qua 6 case domain/layout. Các case độc lập kiểm tra JSON path, alias, split/concat, lỗi cấu hình, unknown source/version, pin input, preview và chặn legacy publish.

Ruff trên các module thay đổi/test S2 đạt; mypy ba module contracts/schema_mapping/mapping_profiles đạt. Suite module boundaries có 3 failures ở dependency có sẵn (core.deps, auth.router, ingestion readers); không tuyên bố toàn repo xanh. Không chạy PostgreSQL/MinIO integration trong S2 vì engine này không có I/O.

