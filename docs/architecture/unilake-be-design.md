# Thiết kế hệ thống UniLake BE

UniLake tiếp nhận dữ liệu Tuyển sinh, Đào tạo và Nhân sự, quản lý các phiên bản Bronze, Silver và Gold, rồi cung cấp báo cáo theo quyền của người dùng. Thiết kế mô tả branch `develop` tại merge commit `768b013`, bao gồm PR #38. IAM mở rộng, Viewer và các thay đổi phân quyền liên quan được mô tả theo mã đang có trong workspace ngày 27/11/2026. Những phần đó đang có thay đổi chưa commit. Giao diện web nằm ở ranh giới tích hợp REST của backend.

[Sơ đồ kiến trúc tương tác](unilake-be-design.html) · [Nguồn sơ đồ Archify](unilake-be-design.architecture.json) · [PR #38](https://github.com/Unilake-AI/unilake-be/pull/38)

## 1. Kiến trúc thành phần

| Thành phần | Trách nhiệm | Mã nguồn |
| --- | --- | --- |
| FastAPI | Đăng ký API theo module, xác thực phiên, kiểm tra quyền, tiếp nhận file, quản lý dataset và trả báo cáo. | [app/main.py](../../app/main.py), [app/core/deps.py](../../app/core/deps.py) |
| Ingestion worker | Nhận job nạp file hoặc đọc nguồn đã đăng ký, xác thực đầu vào, ghi Bronze và chốt thông tin truy vết. | [app/ingestion_worker.py](../../app/ingestion_worker.py), [ingestion](../../app/modules/ingestion) |
| Silver worker | Đọc Bronze, chọn quy tắc theo nguồn, làm sạch và chuẩn hóa bằng DuckDB, cách ly dòng lỗi, kiểm chứng và công bố phiên bản Silver. | [app/silver_worker.py](../../app/silver_worker.py), [pipeline](../../app/modules/pipeline) |
| Gold worker | Tổng hợp từ Silver đã công bố, chốt dữ liệu báo cáo, rồi xử lý tác vụ đồng bộ vào các bảng nghiệp vụ. | [app/gold_worker.py](../../app/gold_worker.py), [đồng bộ Gold](../../app/modules/pipeline/services/gold_business_sync.py) |
| PostgreSQL | Lưu hàng đợi, trạng thái, danh mục, IAM, lineage, dữ liệu nghiệp vụ và số liệu tổng hợp. | [Schema](../../infra/postgres/init/02-schema.sql), [chuỗi migration](../../infra/postgres/migrations/upgrade.psql) |
| MinIO | Lưu các phiên bản file tiếp nhận, artifact Bronze/Silver/Gold, manifest và bằng chứng kiểm chứng. | [MinIO](../../app/infra/minio), [lưu trữ Bronze](../bronze-storage.md) |

API và ba worker là các tiến trình riêng. Worker lấy job từ PostgreSQL qua khóa hàng `FOR UPDATE SKIP LOCKED`. Docker Compose khai báo Redis cùng các dịch vụ trên, còn hàng đợi của luồng dữ liệu nằm trong PostgreSQL. Trong sơ đồ, mũi tên giữa ba worker biểu diễn sự chuyển giao qua job và phiên bản dữ liệu đã chốt. Các worker trao đổi dữ liệu qua MinIO và ghi trạng thái vào PostgreSQL. [Docker Compose](../../docker-compose.yml), [Ingestion repository](../../app/repositories/ingestion_repo.py), [Silver repository](../../app/modules/pipeline/repositories/auto_pipeline.py), [Gold repository](../../app/modules/pipeline/repositories/gold.py)

## 2. API và phân quyền

Các API nghiệp vụ dùng tiền tố `/api/v1`. FastAPI cung cấp Swagger tại `/docs` và gắn `X-Request-ID` để tra cứu lỗi. Với endpoint được bảo vệ, backend đối chiếu JWT Bearer với phiên, trạng thái tài khoản và vai trò hiện hành trong DB. Quyền truy cập dữ liệu còn phụ thuộc vào domain, dataset và phạm vi của từng API.

| Vai trò | Phạm vi |
| --- | --- |
| `ADMIN` | Quản trị tài khoản, vai trò, phân công, nhật ký IAM và grant Domain Owner. Tài khoản vận hành dữ liệu cần vai trò `DATA_ADMIN`. |
| `DATA_ADMIN` | Quản lý nguồn, dataset, profile và chất lượng. Theo dõi và điều khiển pipeline qua API dành cho quản trị dữ liệu. |
| `*_STAFF` | Tải file trong domain được cấp, theo dõi các lần nạp có quyền đọc và xử lý yêu cầu chất lượng thuộc phạm vi được giao. |
| `*_MANAGER` | Đọc báo cáo của dataset hoạt động thuộc cặp domain và phòng sở hữu tương ứng. Grant hợp lệ cho phép duyệt cấu hình làm sạch theo revision và fingerprint. |
| `VIEWER` | Đọc báo cáo tổng hợp, metadata và tình trạng chất lượng của dataset hoạt động thuộc ba domain qua API Viewer. |

Cặp quyền Manager là `hr/PNS`, `academic/PDT` và `admissions/PTS`. Domain Owner cần grant còn hiệu lực và đáp ứng điều kiện về vai trò, nhân viên và phân công. Tài khoản dịch vụ MinIO dùng quyền theo vùng đọc hoặc ghi của từng tác vụ. [Vai trò trong mã nguồn](../../app/core/roles.py), [IAM](../api/admin-iam.md), [API Staff](../api/staff-frontend-api.md), [API Manager](../api/manager-api.md), [API Viewer](../api/viewer-api.md), [tài khoản dịch vụ MinIO](../bronze-service-accounts.md)

## 3. Luồng dữ liệu từ tiếp nhận đến báo cáo

1. **Tiếp nhận.** Client tải CSV/Excel bằng `POST /ingestion/uploads` hoặc tạo lần đọc nguồn đã đăng ký qua `POST /ingestion/jobs`. Với upload, API xác định dataset và domain, giữ file trong vùng tiếp nhận của MinIO, rồi trả HTTP `202` cùng mã ingestion. Job giữ tham chiếu phiên bản và checksum của file. [API ingestion](../../app/modules/ingestion/router.py), [tiếp nhận file](../ingestion-uploads.md)
2. **Bronze.** Worker chọn bộ đọc, kiểm tra cấu trúc và giới hạn đầu vào, rồi lưu file gốc hoặc bản Parquet xuất từ nguồn PostgreSQL. Sau khi đọc lại và xác minh object, worker chốt `SUCCESS` cùng lineage. Cơ sở dữ liệu xếp Silver trong cùng giao dịch cho lần nạp đủ điều kiện. [Thực thi ingestion](../ingestion-execution.md), [chính sách đầu vào Bronze](../bronze-input-validation-policy.md)
3. **Silver.** Worker đọc bản Bronze đã ghim. Profile riêng áp dụng khi khớp nguồn và file, hoặc dùng quy tắc mặc định `auto-source-v1`. Pipeline ánh xạ cột, chuyển kiểu, kiểm tra dữ liệu và lưu dòng lỗi có vị trí nguồn khi xác định được. Luồng mặc định giữ bản Silver tốt hiện hành khi file mới lỗi. Công bố một phần phụ thuộc chính sách của luồng xử lý. Khi công bố thành công, hệ thống ghi snapshot, lineage và xếp Gold. [Silver tự động](../auto-silver-pipeline.md), [truy vết](../data-lineage-contract.md)
4. **Gold báo cáo.** Worker kiểm tra manifest, phiên bản và checksum của Silver. DuckDB tính số bản ghi và các chiều thống kê của công thức đã ghim. Gold ghi Parquet ở MinIO và ghi số liệu, lineage cùng trạng thái công bố vào PostgreSQL. Công bố kiểm tra thế hệ hiện hành để lượt xử lý cũ không ghi đè kết quả mới. [Thiết kế Gold](../gold-pipeline.md), [công thức tổng hợp](../../app/modules/pipeline/services/gold_engine.py)
5. **Đồng bộ nghiệp vụ.** Gold đã công bố có thể xếp `gold_sync_job` để ghi các bảng nghiệp vụ. Đồng bộ đọc artifact chi tiết, kiểm tra tham chiếu và quyền, rồi thực hiện một giao dịch ghi. Chỉ `SYNCED` xác nhận giao dịch nghiệp vụ hoàn tất. [Nhập nhiều bảng](../business-imports-implementation.md), [dịch vụ đồng bộ](../../app/modules/pipeline/services/gold_business_sync.py)

| Mốc | Ý nghĩa với client |
| --- | --- |
| HTTP `202` | API đã tiếp nhận và tạo job. |
| Ingestion `SUCCESS` | Bronze đã xác minh và chốt. |
| Silver `PUBLISHED` hoặc `PUBLISHED_WITH_QUARANTINE` | Snapshot Silver đã công bố. Trạng thái thứ hai có phần dữ liệu bị cách ly. |
| Gold `SUCCEEDED` | Số liệu tổng hợp đã công bố. |
| Đồng bộ `SYNCED`, `BLOCKED` hoặc `FAILED` | Giao dịch nghiệp vụ đã hoàn tất, bị chặn theo quy tắc, hoặc gặp lỗi xử lý. |

## 4. Thiết kế import nghiệp vụ của PR #38

### Xem file và chọn hợp đồng

Staff gọi `GET /ingestion/import-types` để lấy danh sách nghiệp vụ trong domain có quyền. `POST /ingestion/imports/file-layout` đọc tiêu đề theo cấu hình parser. Với workbook nhiều sheet, client chọn `sheet_name` rồi gửi lại để đọc tiêu đề của sheet đó. `POST /ingestion/imports/analyze` nhận `domain`, `columns` và `options` để xác định ánh xạ. Khi trả HTTP `422`, API có thể trả phần ánh xạ đã nhận diện và gợi ý nếu đúng một nghiệp vụ khác phù hợp. Các bước này kiểm tra cấu trúc tiêu đề. Việc xác thực dữ liệu dòng và tra tham chiếu DB diễn ra trong pipeline. [Router](../../app/modules/ingestion/router.py), [đọc cấu trúc file](../../app/modules/ingestion/services/upload_layout.py)

Client gửi `import_options` cùng file, gồm `import_type`, phiên bản hợp đồng và ánh xạ tường minh khi cần. Với hai cột cùng khớp một trường, người dùng chọn cột cần dùng và xác nhận cột bỏ qua qua `ignored_columns`. Hợp đồng chuẩn hóa alias tiêu đề, kiểu dữ liệu và giá trị trạng thái theo từng trường. Profile Silver ghim thông tin quy tắc dùng để xử lý. [Hợp đồng import](../../app/modules/ingestion/business_imports.py), [profile Silver](../../app/modules/pipeline/services/business_import_profile.py)

### Loại import được bổ sung

Cả tám loại mới cần chọn `import_type` tường minh. Khóa bên dưới là khóa nghiệp vụ dùng để tra cứu bản ghi đích.

| `import_type` | Bảng đích và khóa nghiệp vụ | Dữ liệu cần có trước |
| --- | --- | --- |
| `hr_position_catalog` | `position` theo mã chức vụ | |
| `hr_qualification_catalog` | `qualification` theo tên văn bằng | |
| `admissions_year_catalog` | `admission_year` theo nhãn năm tuyển sinh | |
| `admissions_method_catalog` | `admission_method` theo mã phương thức | |
| `academic_student` | `student` theo mã sinh viên | Chương trình, năm tuyển sinh |
| `academic_class` | `class` theo mã lớp | Học phần, học kỳ |
| `academic_enrollment` | `enrollment` theo sinh viên và lớp | Sinh viên và lớp đã ghi DB |
| `academic_enrollment_grade` | `enrollment_grade` theo lượt đăng ký | Lượt đăng ký đã ghi DB |

Với Đào tạo, nạp hồ sơ sinh viên và lớp trước, sau đó nạp đăng ký rồi đến bảng điểm. Bảng điểm kiểm tra đăng ký tồn tại, ít nhất một giá trị điểm, thang điểm và ngày đăng ký nếu được gửi. Nhân sự nạp danh mục trước phân công và bằng cấp. Tuyển sinh nạp năm và phương thức trước hồ sơ đăng ký. Hệ thống kiểm tra các đợt đang mở có mốc thời gian giao nhau, khóa việc đổi thang điểm gây xung đột với kết quả đã lưu và chặn đăng ký mới theo phương thức đã tắt. [Đặc tả nhập dữ liệu](../business-imports-implementation.md), [repository import](../../app/repositories/business_import_repo.py)

### Lập kế hoạch và giao dịch ghi

Với hợp đồng có bước lập kế hoạch, worker gom dòng theo khóa, tra danh mục theo lô, xác định thao tác `inserted`, `updated` hoặc `unchanged` rồi lưu bản xem trước. Kế hoạch đầy đủ nằm trong MinIO và gắn với checksum của artifact nguồn. Worker kiểm tra lại quyền và lập lại kế hoạch trong giao dịch ghi. Nếu kế hoạch thay đổi, tác vụ bị chặn với `IMPORT_PLAN_STALE`. Khi hợp lệ, worker ghi bản ghi cha trước, dùng khóa đích cho bản ghi con và chốt đối chiếu, audit cùng kết quả trong một giao dịch. Cập nhật giữ giá trị cũ ở ô trống theo `blank_update=preserve`. [Repository import](../../app/repositories/business_import_repo.py), [đồng bộ Gold](../../app/modules/pipeline/services/gold_business_sync.py)

Client dùng `GET /ingestion/jobs/{job_id}/business-import` để đọc `database_committed`, mã lỗi và chi tiết theo dòng/cột. Endpoint `/business-import/preview` trả `PENDING`, `READY`, `BLOCKED` hoặc `NOT_AVAILABLE`. Khi kế hoạch sẵn sàng và hợp lệ, worker tự tiếp tục ghi. Với lỗi tham chiếu hoặc kế hoạch cũ, quản trị có thể thử lại đồng bộ sau khi xử lý nguyên nhân. [API trạng thái import](../../app/modules/ingestion/services/business_imports.py), [API Gold](../gold-pipeline.md)

## 5. Mô hình lưu trữ và quan hệ dữ liệu

| Vị trí | Dữ liệu |
| --- | --- |
| MinIO tiếp nhận và Bronze | Phiên bản file gốc hoặc Parquet trích xuất, checksum và metadata để worker truy về lần nạp. |
| MinIO Silver | Parquet đã chuẩn hóa, manifest, artifact cách ly, metadata quản lý phiên bản và artifact nghiệp vụ riêng khi hợp đồng cần. |
| MinIO Gold | Parquet tổng hợp, manifest và artifact chi tiết phục vụ đồng bộ nếu áp dụng. |
| PostgreSQL `app` | IAM, nguồn và dataset, hàng đợi, lineage, thông tin chất lượng, bảng nghiệp vụ, định danh và nhật ký import. |
| PostgreSQL `gold` | `gold.aggregate` lưu số liệu theo job, thực thể, chỉ số và chiều. `gold.current_metrics` chỉ lấy dữ liệu công bố khớp snapshot Silver hiện hành. |

- **Tuyển sinh.** `admission` liên kết thí sinh, chương trình, năm và phương thức. Import kết quả và xác nhận nhập học đối chiếu `expected_revision`, quyền và trạng thái. Xác nhận nhập học cập nhật hồ sơ tuyển sinh. Việc tạo hồ sơ sinh viên là thao tác riêng. [Quy trình tuyển sinh](../admissions-workflows.md)
- **Đào tạo.** `curriculum_course` liên kết chương trình đào tạo phiên bản hóa với học phần. Một sinh viên có một lượt `enrollment` cho mỗi lớp, và mỗi lượt có tối đa một `enrollment_grade`. [Schema PostgreSQL](../../infra/postgres/init/02-schema.sql)
- **Nhân sự.** `assignment` liên kết nhân viên, phòng và chức vụ. `lecturer` dùng `employee_id` làm khóa. `employee_qualification` liên kết nhân viên với danh mục văn bằng. [Schema PostgreSQL](../../infra/postgres/init/02-schema.sql)

Các định danh nguồn, locator dòng và quan hệ lineage cho phép truy từ báo cáo về snapshot, lần nạp và phiên bản file. Trường cá nhân được đánh dấu trong hợp đồng bị loại khỏi tập phân tích và có thể nằm trong artifact nghiệp vụ riêng dùng cho đồng bộ. API lỗi và preview trình bày lỗi, số liệu và vị trí dòng theo hợp đồng trả về. [Lineage](../data-lineage-contract.md), [thiết kế import](../business-imports-implementation.md)

## 6. Báo cáo, chất lượng và vận hành

- **Báo cáo.** Manager và Viewer đọc số liệu Gold hiện hành qua API theo phạm vi riêng. Chỉ số `record_count` đếm bản ghi trong một scope nguồn. Chỉ số đếm khóa phân biệt chỉ có ở công thức đã khai báo. Khi Silver đổi mà Gold mới chưa công bố, hệ thống loại Gold cũ khỏi view hiện hành. [API Manager](../api/manager-api.md), [API Viewer](../api/viewer-api.md), [Gold](../gold-pipeline.md)
- **Chất lượng.** Data Admin theo dõi lần chạy, bản ghi cách ly và yêu cầu sửa. Staff xử lý trong phạm vi được giao. Trạng thái `VERIFIED` xác nhận việc sửa đã được kiểm chứng. Trạng thái công bố Silver và Gold được theo dõi qua pipeline. [Quản lý Silver](../silver-management-api.md), [theo dõi Bronze tới Silver](../api/pipeline-flow-monitor.md)
- **NLQ.** API `POST /nlq/ask` đã đăng ký. Provider hiện trả `503 NLQ_UNAVAILABLE`. Báo cáo hiện hành dùng các chỉ số Gold đã tính. [Provider NLQ](../../app/providers.py), [router NLQ](../../app/modules/nlq/router.py)
- **Khôi phục.** Ingestion ghi heartbeat và lịch sử theo `root_job_id`. Lỗi hạ tầng tạm thời có thể tạo lượt thử mới. Nếu có checkpoint Bronze, worker kiểm tra lại đúng đầu vào và dùng lại object đã xác minh. Lỗi dữ liệu cần sửa file, danh mục hoặc cấu hình trước khi chạy lại. Silver và Gold dùng lease, snapshot hoặc thế hệ để ngăn lượt cũ công bố sai. [Tin cậy ingestion](../ingestion-reliability.md), [Silver tự động](../auto-silver-pipeline.md), [Gold](../gold-pipeline.md)
- **Triển khai.** Docker Compose cấu hình API, ba worker, PostgreSQL và MinIO. Với database đã có dữ liệu, cập nhật theo chuỗi migration trước khi chạy API và worker tương ứng. `/health` xác nhận API phản hồi. Heartbeat worker và trạng thái job cung cấp bằng chứng riêng về khả năng xử lý. [Hướng dẫn Docker](../docker-guide.md), [migration](../database-migrations.md), [cấu hình Compose](../../docker-compose.yml)

## 7. Kịch bản nghiệm thu

| Kịch bản | Kết quả cần quan sát | Kiểm thử tham chiếu |
| --- | --- | --- |
| Staff đọc CSV hoặc chọn sheet Excel | Trả tiêu đề đúng cấu hình. Ánh xạ mơ hồ báo lỗi và chấp nhận lựa chọn tường minh. | [test_staff_import_layout.py](../../tests/test_staff_import_layout.py) |
| Nạp sinh viên, lớp, đăng ký và bảng điểm | Chặn bảng điểm khi thiếu đăng ký. Nạp hợp lệ ghi đúng bảng. Nạp lại giữ bản ghi, file sửa điểm cập nhật bản ghi tương ứng. | [test_business_imports_live.py](../../tests/test_business_imports_live.py) |
| Nạp danh mục Nhân sự và Tuyển sinh | Phân công, bằng cấp và đăng ký dùng đúng danh mục. Các thay đổi xung đột bị chặn trước ghi. | [test_business_imports.py](../../tests/test_business_imports.py), [test_business_imports_live.py](../../tests/test_business_imports_live.py) |
| Job lỗi hoặc lượt Silver mới | Lịch sử thử lại và phiên bản dữ liệu được giữ. Gold hiện hành khớp snapshot Silver mới nhất đã công bố. | [test_ingestion_reliability.py](../../tests/test_ingestion_reliability.py), [test_gold_pipeline.py](../../tests/test_gold_pipeline.py) |
| Vai trò đọc báo cáo và thực hiện IAM | Dữ liệu trả về đúng phạm vi role, domain và phòng sở hữu. Quyền từ token được đối chiếu với DB. | [test_manager_api.py](../../tests/test_manager_api.py), [test_viewer_api.py](../../tests/test_viewer_api.py), [test_authorization_deps.py](../../tests/test_authorization_deps.py) |
