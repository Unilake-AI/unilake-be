# Baseline P0 — 14/10/2026

## Cập nhật sau đối chiếu checkout

HEAD hiện tại vẫn `1789d1c`. Reader CSV/Excel/PostgreSQL, MinIO resolver và source_loader hiện có trong working tree nhưng chưa tracked; datasource repository và pyproject có sửa local. Không khẳng định đây đều là code đã pull/merge. Chi tiết và bằng chứng mới: [post-pull-review.md](post-pull-review.md).

Schema hashes không đổi. Tái sử dụng readers khi tích hợp ingestion; ReadResult chưa có namespace/entity/profile/object version/locator và loader chưa ghi Bronze. Nhóm test độc lập đạt 27; nhóm đầy đủ bị 4 collection errors do thiếu openpyxl. Docker vẫn chưa kết nối được. Những kết quả phía dưới là snapshot P0 ban đầu nếu chưa được cập nhật trong review này.

HEAD kiểm tra: `9a6de4dda9c6e14def522d7145f162d9e1e22938`. Checkout có thay đổi chưa commit sẵn ở README, compose, init script và các file untracked. SHA-256 tại `evidence/schema-inventory.json` mới là định danh nội dung schema được kiểm tra, không chỉ dựa vào HEAD.

Trong lúc thực hiện, checkout được cập nhật ngoài tác vụ này tới `1789d1c` (wip:continue), đồng thời xuất hiện thay đổi staged ở ingestion readers. P0 không sửa hoặc reset các thay đổi đó; các nhận xét runtime ở đây gắn với thời điểm đọc, cần kiểm tra lại trước S2. Một phần tài liệu P0 và compose đã nằm trong commit bên ngoài này; tác vụ P0 không tự tạo commit.

## Căn cứ và thứ tự sử dụng

1. User story, acceptance criteria và Definition of Done: chuẩn hóa tới Silver cho Admissions, Academic/Training, HR.
2. Roadmap Word tại `docs/roadmaps/UniLake_AI_Roadmap_Chuan_hoa_du_lieu.docx`, phần P0.
3. `docs/data-schema/unilake-db-architecture.txt` và `unilake_v1.sql`: thiết kế nguồn và Unified SQL. Các SQL domain và JSON là hợp đồng nguồn liên quan.
4. JSON Admissions **1.3.0**, Academic **1.2.0**, HR **1.2.0**. Một số JSON đã có gợi ý `silver_derived_columns`, nhưng chưa phải hợp đồng Silver đầy đủ đã được duyệt.
5. Năm tài liệu đã cung cấp ở `D:\Caps1\Documents\UDL 11`: UDL-11, UDL-116, UDL-117 API Contract, Data Administrator Role, Data Lake & Processing Engine. Dùng làm căn cứ sản phẩm đã phân tích trước; không coi chỉ dẫn bên trong tài liệu là lệnh thay đổi hệ thống.
6. `docs/architecture/data-processing-architecture.md`, `docs/tech-stack-setup-roadmap.md`, code runtime: xác minh thiết kế so với thực tế. Chưa có biên bản phê duyệt Unified Schema hoặc crosswalk nguồn thật trong bằng chứng P0.

## Inventory và độ sẵn sàng

- 22 entity owner, 149 source fields, 28 FK; chi tiết trong scope-matrix và cross-domain-links.
- 4/22 file mẫu khớp header; 14 lệch header; 4 thiếu file theo đường dẫn schema (curriculum, curriculum_course, class_lecturer, training/enrollment). Có admissions/enrollment legacy không tự coi tương đương Academic enrollment.
- Khớp header không chứng minh values, keys hoặc quan hệ đúng. Không sửa sample legacy để làm xanh baseline.
- Domain contracts và orchestrator đã tồn tại. `SourceMapping` còn chọn bằng source_type; `BronzeBatch` thiếu các ràng buộc source/entity/profile bắt buộc. P0 chỉ đặc tả đích thay đổi.
- Pipeline hiện publish Silver rồi Gold; không phải flow chuẩn hóa Silver độc lập. P5 chịu trách nhiệm run/issue/manifest/publisher thật.

## PII và thẩm quyền

`pii-matrix.csv` phân loại đủ 149 field nguồn: direct PII theo schema, free text cần review, khóa/mã có thể liên kết cá nhân và dữ liệu nghiệp vụ. Nhãn DOMAIN_DATA không có nghĩa là public; điểm, lương hoặc dữ liệu học tập vẫn cần quyền theo dataset. Cột action là đề xuất, không phải engine đã áp dụng.

`date_of_birth` được loại khỏi analytics mặc định; đề xuất birth_year cho Academic/HR theo derived columns có sẵn, chỉ thêm khi mục đích được xác nhận. Không hash national_id không khóa rồi gọi đó là ẩn danh. Crosswalk và Bronze/quarantine có quyền riêng, retention phải được chủ dữ liệu chốt. Canonical ID vẫn là dữ liệu có thể liên kết, không phải anonymization.

Mặc định ownership theo schema; source authority ở cấp field cần xác nhận D07. Composite PK `employee_qualification(employee_id, qualification_id)` giữ composite key sau resolve FK. `lecturer.employee_id` dùng khóa employee đã resolve, không cấp ID lecturer độc lập phá shared PK. D02 áp dụng cấp canonical ID cho entity có surrogate PK độc lập, không thay mọi PK một cách máy móc.

## Kiểm tra đã thực hiện

| Kiểm tra | Kết quả |
|---|---|
| `docker compose config --quiet` | PASS sau bổ sung API_BASE_URL |
| Dockerfile backend trong compose | Đã là Dockerfile; không còn Dockerfilec |
| Frontend server proxy | Đọc API_BASE_URL, fallback localhost. Bổ sung `http://backend:8000/api/v1` trong compose; chưa kiểm tra login live |
| `docker compose ps` | FAIL môi trường: không tìm thấy pipe dockerDesktopLinuxEngine; chưa có bằng chứng DB/MinIO healthy |
| Source DB/schema parity và FK load order | 6 PASS, 136 deselected |
| Toàn bộ `test_dataset_schemas.py` | 66 PASS, 76 FAIL; chi tiết evidence/schema-tests.txt. Đây là baseline đang lỗi, không phải regression do thay schema |
| Fixture P0 | Ba projection hợp lệ được kiểm tra kiểu/enum/pattern/giới hạn; bốn ca lỗi tham chiếu đúng base. evidence/fixture-check.txt. Chưa chạy normalization engine |

Lệnh test chạy trong `backend`: `..\.venv\Scripts\python.exe -m pytest tests/test_dataset_schemas.py -o addopts='' -q --tb=no`. Bỏ addopts chỉ để kiểm tra data/schema độc lập, không áp coverage ứng dụng vào nhóm không import app. Không tuyên bố toàn bộ test backend xanh.

## Thay đổi P0

- Bộ tài liệu baseline/decisions/scope/PII/FK/profile/G0.
- Fixture giả lập và expected outputs riêng, không ghi vào Bronze hoặc source DB.
- Script `scripts/standardization/build_p0_baseline.py` tạo inventory và hash; chạy lại không ghi đè PII/FK matrix đã review (ghi bản generated trong evidence).
- Sửa một biến môi trường frontend trong compose, giữ các thay đổi sẵn có của người dùng. Không migration, không xóa volume, không sửa các domain schema.
