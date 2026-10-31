# Đối chiếu P0 sau cập nhật checkout — 14/10/2026

## Kết luận

Giữ nguyên scope 22 entity, 149 field, 28 FK và D01–D09. Cần cập nhật baseline ingestion, contract handoff và tiêu chí môi trường G0. G0 vẫn NOT PASSED.

HEAD quan sát là `1789d1c` (`wip:continue`), chưa có commit mới hơn HEAD ghi nhận cuối lượt P0. So với `9a6de4d`, commit này thêm tài liệu P0/roadmap, seed init, README và sửa compose. Không thể quy mọi thay đổi cho pull: ingestion readers/tests hiện là **untracked**, datasource_repo.py và pyproject.toml là **modified local**. Không sửa, stage hoặc reset các file đó trong lượt review.

## Những thay đổi ảnh hưởng P0

| Nội dung | Bằng chứng checkout | Điều chỉnh |
|---|---|---|
| Đọc nguồn đã có implementation | CSVReader, ExcelReader, PostgreSQLReader, factory, request_builder, source_loader | Tái sử dụng lớp đọc nguồn; không lập kế hoạch viết lại reader tại S2 |
| Loader chưa phải ingestion hoàn chỉnh | read_data_source trả ReadResult, không tạo job, không ghi Bronze/status | Giữ dependency adapter ReadResult → BronzeBatch; chưa nghiệm thu Bronze/Silver end-to-end |
| Metadata chưa đủ | ReadResult chỉ columns/rows/source_table; source system được kiểm tra nhưng không đi theo kết quả | Adapter phải giữ namespace/domain/entity/profile, source locator và object version/checksum |
| S3 chưa pin version | fget_object không truyền version_id; query/fragment URI bị từ chối | Bổ sung trường version riêng và checksum tại handoff; đường dẫn s3 không đủ replay xác định |
| CSV và Excel có biểu diễn khác nhau | CSV all_varchar=True; Excel values_only/data_only=True; PostgreSQL trả giá trị typed | S3 nhận cả chuỗi và kiểu native. Test null, date, Decimal; Excel code dạng số mất 0 đầu không được tự phục hồi bằng đoán |
| Reader type chưa đồng nhất catalog | Repository chấp nhận cấu hình RDBMS; factory/request_builder hỗ trợ CSV, EXCEL, POSTGRESQL và alias POSTGRES | Chốt engine cụ thể cho RDBMS rồi dispatch; không đổi mọi RDBMS thành PostgreSQL |
| Dependency Excel | Factory import ExcelReader ngay; openpyxl nằm trong extra ingest-docs | Môi trường hiện thiếu openpyxl nên cả loader/factory không import được, kể cả luồng CSV qua factory |
| Python target lệch | mypy python_version=3.13; requires-python >=3.11, Ruff py311, Docker Python 3.11 | Bổ sung quyết định thống nhất target; chưa tự nâng runtime hoặc sửa cấu hình local của người dùng |
| Mẫu reader khác source schema | Integration fixture applicant_id/full_name/status | Dùng test parser, không dùng làm mẫu chuẩn applicant Silver hoặc thay fixtures P0 |

Excel bỏ dòng trống và trim header nên index của rows trả về không phải số dòng vật lý của workbook. Cần giữ sheet và physical row locator trước khi bỏ dòng; không suy locator từ enumerate(ReadResult.rows). CSV cũng cần xét skip_rows/header và bản ghi nhiều dòng. PostgreSQL dùng PK cùng snapshot/extraction metadata thay cho số thứ tự SELECT không ORDER BY.

## Kiểm tra thực tế

- SHA-256 toàn bộ schema/SQL trong baseline: không thay đổi. Không cần sinh lại scope/PII/FK chỉ vì code reader mới.
- `docker compose config --quiet`: PASS. API_BASE_URL đã có; không sửa lại.
- `docker compose ps -a`: chưa kết nối được dockerDesktopLinuxEngine; chưa xác minh PostgreSQL/MinIO.
- Nhóm CSV/PostgreSQL/request_builder/MinIO resolver: **27 passed**. PostgreSQL/MinIO tests trong nhóm này không chứng minh kết nối dịch vụ thật.
- Nhóm đầy đủ tám file reader/loader: **4 collection errors**, đều liên quan thiếu openpyxl. Không được báo suite pass hoặc hai integration cases pass.
- Real MinIO test chỉ chạy khi RUN_MINIO_INTEGRATION=1, phụ thuộc catalog ID 1/2 và objects cụ thể. Cần cấu hình fixture môi trường tường minh trước chạy; không tùy tiện ghi đè catalog để khớp ID.
- Số 66 pass/76 fail của schema/sample là bằng chứng lượt P0 trước, không chạy lại ở lượt review này. Schema hash không đổi; không dùng điều đó để suy toàn bộ sample/tests không đổi.

## Thứ tự tiếp tục

1. Hoàn thiện dependencies/runtime của reader; chạy lại toàn bộ nhóm test và integration khi Docker sẵn sàng.
2. Chốt RDBMS dispatch và giao metadata adapter cho ingestion owner; bổ sung locator/version trước khi gọi đó là Bronze handoff có replay.
3. Tiếp tục S1 với scope hiện có; không cần viết lại schema chỉ vì thêm reader. Những quyết định PII/authority còn PROPOSED giữ nguyên.

Đây là cập nhật tài liệu P0 và phụ thuộc tích hợp. Chưa triển khai fixes cho reader, chưa nâng dependency/runtime và chưa đổi roadmap Word.
