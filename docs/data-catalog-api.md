# API quản trị Data Catalog

Tất cả endpoint yêu cầu Bearer access token có role `DATA_ADMIN` (kể cả `ADMIN` cũng không được truy cập nếu không có role này).

## Chuẩn bị database

Database mới được khởi tạo qua Docker sẽ có bảng `app.catalog_metadata` trong `infra/postgres/init/02-schema.sql`.
Database đã tồn tại cần chạy file `infra/postgres/migrations/20261103_catalog_metadata.sql` bằng `psql -v ON_ERROR_STOP=1` trên đúng database backend trước khi khởi động phiên bản này. Migration có thể chạy lại, không xóa dữ liệu cũ. Không cần chạy lại seed hay xóa volume.

## Endpoint

Prefix mặc định: `/api/v1/governance`.

| Method | Path | Kết quả |
|---|---|---|
| GET | `/catalog` | Danh sách metadata hiệu lực; query `domain_id` là tùy chọn |
| GET | `/catalog/{domain_id}/{dataset}` | Metadata một dataset |
| POST | `/catalog` | Thêm metadata, trả 201 |
| PATCH | `/catalog/{domain_id}/{dataset}` | Sửa một phần metadata, trả 200 |
| DELETE | `/catalog/{domain_id}/{dataset}` | Xóa mềm metadata, trả 204 không có body |

Ví dụ tạo metadata trong lĩnh vực `demo` đã được đăng ký:

```http
POST /api/v1/governance/catalog
Authorization: Bearer <access_token>
Content-Type: application/json
```

```json
{
  "domain_id": "demo",
  "dataset": "student_profiles",
  "layer": "silver",
  "schema_version": "1.0",
  "description": "Thông tin sinh viên đã chuẩn hóa",
  "fields": [
    {"name": "student_id", "data_type": "string", "nullable": false, "description": "Mã sinh viên"},
    {"name": "name", "data_type": "string", "nullable": false, "description": "Tên sinh viên"},
    {"name": "status", "data_type": "string", "nullable": true, "description": "Trạng thái"}
  ]
}
```

Sửa mô tả bằng `PATCH /api/v1/governance/catalog/demo/student_profiles`:

```json
{"description": "Hồ sơ sinh viên đang theo học"}
```

Field không gửi sẽ giữ nguyên. `fields` nếu gửi sẽ thay toàn bộ danh sách cột; dùng danh sách này để thêm/sửa/xóa metadata cột. `fields: []` làm rỗng danh sách, `description: ""` xóa nội dung mô tả. Không nhận `null`, field lạ hay hai cột trùng tên. `domain_id` và `dataset` là khóa cố định; PATCH không đổi hai giá trị này. `layer` nhận `bronze`, `silver`, `gold`; `data_type` là chuỗi metadata, không dùng để chạy DDL.

Xóa bằng `DELETE /api/v1/governance/catalog/demo/student_profiles`. Có thể POST lại cùng khóa để khôi phục bằng metadata mới.

Mã lỗi: 401 khi thiếu/token sai; 403 khi thiếu role; 404 khi domain/dataset không tồn tại; 409 khi POST trùng dataset đang hiển thị; 422 khi payload không hợp lệ. Domain phải có trong domain registry; API này không tạo lĩnh vực mới.

## Luồng code để đọc

1. `app/modules/governance/router.py`: kiểm tra role, nhận payload, gọi service; get_db quản lý commit/rollback.
2. `schemas.py`: validate payload và tên cột; PATCH phân biệt bỏ qua field với gửi null.
3. `catalog_service.py`: ghép metadata nền từ domain registry với bản quản trị trong DB; thực hiện CRUD và kiểm tra tồn tại/trùng.
4. `repository.py`: SELECT/UPSERT bằng SQLAlchemy; khóa ghép `(domain_id, dataset)` chặn trùng.
5. `models.py`: model `CatalogMetadata` lưu JSON và dấu xóa mềm.

GET ưu tiên bản trong DB hơn metadata khai báo trong code. DELETE ghi dấu xóa mềm để khai báo tĩnh không xuất hiện lại sau restart. Thay đổi được lưu trong PostgreSQL; không lưu vào biến toàn cục hay sửa file Python khi có request.

Các thao tác chỉ tác động metadata hiển thị của Data Catalog. Chúng không tạo/xóa bảng vật lý, không xóa dữ liệu, không thay đổi bảng `catalog_table`/`catalog_column`, không sửa hợp đồng schema của pipeline hay ngữ cảnh NLQ từ registry. Cập nhật mô tả kiểu cột ở đây không tự đổi kiểu cột thực tế.

Kiểm thử: `tests/test_governance_api.py` dùng JWT thật và SQL trên SQLite cô lập, không kết nối database đang dùng. Chưa thay thế kiểm thử triển khai migration trên PostgreSQL thực tế.
