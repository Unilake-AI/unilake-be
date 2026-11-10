# Luồng dữ liệu nguồn thật → Silver

Silver làm sạch dữ liệu và thống nhất cấu trúc để chuẩn bị xử lý Gold. Các phép tính như GPA, điều kiện trúng tuyển, thâm niên và chỉ số báo cáo thuộc bước xử lý nghiệp vụ sau Silver. Danh sách 39 quy định nghiệp vụ trong tài liệu cũ không còn là điều kiện bắt buộc để công bố Silver.

## Luồng đang triển khai

1. Đăng ký nguồn và bộ dữ liệu Bronze trong danh mục hiện có.
2. Thu thập qua API ingestion hoặc [tải file trực tiếp](../ingestion-uploads.md). Worker lưu bản gốc vào MinIO, ghi nhận phiên bản tệp, mã kiểm tra nội dung, số dòng và cách đọc tệp. Chỉ lần thu thập `SUCCESS` được dùng tiếp, gồm cả job thu thập `EXECUTION_V1` và tải lên `UPLOAD_V1`.
3. Quản trị viên chọn cấu hình nguồn và gọi API làm sạch. Cấu hình phải khớp mã nguồn, bộ dữ liệu, hệ thống nguồn, tên tệp, loại tệp và lĩnh vực của lần thu thập.
4. Đọc đúng phiên bản Bronze với cách đọc đã được lưu khi thu thập. Kiểm tra lại kích thước, nội dung và số dòng.
5. Xử lý ô thiếu, định dạng không hợp lệ và bản ghi trùng theo cấu hình. Dữ liệu Bronze được giữ nguyên. Khi còn bản ghi lỗi, lưu hồ sơ cách ly và báo cáo; chặn công bố cả lô. Chưa cho phép công bố một phần.
6. Đổi tên cột, chuẩn hóa kiểu dữ liệu, đối chiếu mã nguồn sang mã chung và thống nhất giá trị bằng bảng đối chiếu được khai báo.
7. Kiểm tra cấu trúc, kiểu, mã định danh không trùng và liên kết giữa các bảng. Thay toàn bộ nội dung bảng được cập nhật; giữ các bảng khác từ bộ dữ liệu đã công bố trước đó. Không cập nhật từng dòng theo mã.
8. Ghi Parquet và bản kê nguồn gốc vào MinIO Silver. Đọc lại để xác minh trước khi công bố. PostgreSQL lưu lịch sử và bộ dữ liệu đang được sử dụng; không chứa các dòng Silver thay cho Parquet.
9. Gold có thể đọc bộ Silver đã công bố để biến đổi, tổng hợp và tính chỉ số. Chưa có bước tự động chạy Gold.

API Silver vẫn được gọi riêng sau ingestion; chưa có worker tự động nối hai bước. Hỗ trợ CSV, Excel `.xlsx`, `.xlsm`. PostgreSQL đã được thu thập vào Bronze nhưng chưa có bộ đọc bản Bronze dạng Parquet cho luồng Silver này.

## Khai báo cấu hình nguồn

Cấu hình có thể được tạo/sửa qua API `/api/v1/pipeline/profiles` và lưu bền vững trong PostgreSQL, theo [hướng dẫn quản lý Silver](../silver-management-api.md). Cấu hình khai báo sẵn trên máy chủ trong `config/silver/profiles.json`, hoặc tệp được chỉ định bằng biến `SILVER_PROFILES_PATH`, vẫn được hỗ trợ; bản PostgreSQL được ưu tiên khi trùng mã. Cấu hình không chứa mật khẩu hay thông tin kết nối. Cấu trúc tệp được mô tả trong `config/silver/profiles.schema.json`. Tệp mặc định là `{}`: không có nguồn hoặc mã đối chiếu được tạo sẵn.

Nội dung là một đối tượng JSON: mỗi khóa là tên cấu hình để truyền qua `profile_id`; mỗi giá trị gồm:

| Thuộc tính | Nội dung cần khai báo |
|---|---|
| `data_source_id`, `dataset_id`, `source_system_id` | Mã nguồn, bộ dữ liệu Bronze và hệ thống nguồn đã đăng ký |
| `source_type`, `source_table`, `domain`, `entity` | CSV/EXCEL, tên tệp, lĩnh vực và bảng Silver nhận dữ liệu |
| `version`, `source_schema_version` | Phiên bản quy tắc và cấu trúc nguồn |
| `scope` | Tên bộ Silver dùng chung cho các bảng liên quan, tối đa 40 ký tự |
| `source_schema` | Cấu trúc nguồn gồm `entities`, tên cột, kiểu, cho phép trống, khóa và giới hạn; cùng dạng các tệp `docs/data-schema/*_v1.schema.json` |
| `contracts` | Cấu trúc Silver theo từng lĩnh vực, cùng dạng `docs/standardization/silver-schemas/*.json`. Phải có cả cấu trúc các bảng được tham chiếu |
| `field_mapping` | Tên cột Silver → tên cột nguồn, khai báo rõ từng cột |
| `ignored_columns` | Cột nguồn chỉ giữ ở Bronze, không chuyển vào Silver |
| `format_options` | Quy tắc định dạng theo tên cột đích: bỏ khoảng trắng, định dạng ngày, dấu thập phân, danh sách giá trị tương đương… |
| `type_options` | Quy tắc chuyển kiểu theo tên cột đích, gồm múi giờ nguồn khi cần |
| `null_tokens` | Các giá trị được coi là ô trống theo từng cột đích; mặc định chỉ chuỗi rỗng |
| `dictionaries` | Tên cột có danh sách giá trị chuẩn → bảng giá trị nguồn/giá trị chuẩn. Kể cả khi không có bí danh, cột có danh sách giá trị chuẩn cần khai báo `{}` |
| `reference_namespaces` | Cột liên kết → mã hệ thống nguồn dùng để tìm bản ghi liên quan |
| `crosswalk_release`, `effective_at` | Phiên bản bảng đối chiếu mã đã lưu trong PostgreSQL và thời điểm áp dụng có múi giờ |
| `collapse_exact_duplicates` | Có gộp bản ghi trùng hoàn toàn cùng mã không; mặc định `false` |
| `source_preview_columns` | Danh sách cột nguồn cho phép xem trong hồ sơ lỗi; mặc định rỗng, không mở xem giá trị gốc |

Các cột nguồn phải khớp danh sách được ánh xạ hoặc được bỏ qua. Không tự đoán tên cột. Bản đầu tiên này yêu cầu các trường dữ liệu Silver được ánh xạ đầy đủ; không tự suy ra năm sinh hay các chỉ số khác từ cột bị bỏ qua.

Mỗi lần chạy ghi nhận mã nội dung cấu hình trong phiên bản quy tắc. Sửa cấu hình làm thay đổi mã này, tránh dùng nhầm kết quả cũ. Cấu trúc Silver thay đổi cần phiên bản mới và kế hoạch chuyển đổi; không tự trộn cấu trúc khác nhau trong cùng bộ Silver.

## Bảng đối chiếu mã

Dùng kho đối chiếu PostgreSQL đã có. Công cụ quản trị nhận JSON gồm `version` và `entries`. Mỗi phần tử gồm `source_system_id`, `entity`, `source` (danh sách chứa mã gốc), `evidence` (căn cứ đối chiếu), `valid_from`, tùy chọn `valid_to` và `canonical_id`.

```sh
.venv/bin/python scripts/standardization/register_crosswalk.py --input /duong-dan/doi-chieu-ma.json
```

Không truyền `canonical_id` thì cấp mã mới, không tự nối hai nguồn theo tên. Muốn dùng mã chung đã tồn tại phải cung cấp căn cứ đối chiếu. Giảng viên phải dùng mã nhân sự đã có. Công cụ ghi cả danh sách và phiên bản trong cùng một lần lưu; không tạo bảng hoặc chạy migration tự động.

## Gọi xử lý Silver

`POST /api/v1/pipeline/cleaning/ingestion-jobs/{ingestion_job_id}` yêu cầu quyền `ADMIN` hoặc `DATA_ADMIN`.

```json
{
  "profile_id": "ten-cau-hinh-da-khai-bao",
  "base_snapshot": "active"
}
```

`active` lấy bộ Silver hiện hành của phạm vi đã cấu hình. Có thể truyền mã bộ Silver cụ thể; `null` chỉ dành cho lần công bố đầu tiên khi phạm vi chưa có dữ liệu. Nếu dữ liệu hiện hành đã thay đổi bởi lần chạy khác, hệ thống từ chối ghi đè.

Lịch sử và báo cáo tiếp tục xem qua `/api/v1/cleaning-runs`; hồ sơ dòng lỗi qua `/api/v1/quarantine-records`. Dòng nguồn chỉ được xem khi cột đó nằm trong `source_preview_columns` đã lưu của chính lần chạy. Các hồ sơ cũ chưa có danh sách này bị chặn xem giá trị nguồn.

## Điều kiện sử dụng và giới hạn

- Cần PostgreSQL/MinIO hoạt động và các migration ingestion, đối chiếu mã, Silver, lịch sử làm sạch và cách ly đã được áp dụng.
- Các lần thu thập trước thay đổi này chưa có `source_type`/`parser_options` trong kết quả sẽ bị từ chối với `BRONZE_PARSER_EVIDENCE_REQUIRED`. Cần thu thập lại để ghi nhận cách đọc chính xác; không suy từ cấu hình nguồn hiện tại.
- Nhãn phê duyệt dự thảo trong tài liệu cũ không còn là cổng công bố Silver; cấu trúc và kiểu dữ liệu vẫn được kiểm tra nghiêm ngặt. Người quản trị chịu trách nhiệm khai báo cấu hình nguồn được sử dụng.
- Dữ liệu giả lập chỉ dùng trong kiểm thử; không có miền dữ liệu mẫu tự đăng ký vào danh mục ứng dụng. Miền phục vụ danh mục/Gold phải được đăng ký bằng định nghĩa thực tế.
- Chưa triển khai giao diện cấu hình, lịch chạy tự động, xử lý tăng dần hoặc xử lý dữ liệu lớn theo luồng. Các bước hiện nạp dữ liệu vào bộ nhớ.
- Thay đổi mã nguồn không xóa dữ liệu đã có trong PostgreSQL/MinIO và không tự triển khai lên môi trường đang chạy.

## Kiểm thử

Chạy từ thư mục gốc dự án:

```sh
.venv/bin/python -m pytest --no-cov -q tests/test_source_cleaning.py tests/test_silver_publication.py tests/test_cleaning_pipeline.py tests/test_quarantine_records.py tests/test_lineage_api.py
```

Các kiểm thử dùng dữ liệu riêng, không kết nối hoặc sửa dữ liệu đang vận hành.
