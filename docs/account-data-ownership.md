# Cấu hình tài khoản và quyền sở hữu dữ liệu

ADMIN dùng Người dùng & Phân quyền để liên kết tài khoản với hồ sơ nhân viên đang hoạt động, xem các phân công và thêm phân công phòng ban/chức vụ theo ngày hiệu lực. Chỉ chọn người có thật; nếu thiếu hồ sơ cần nạp hồ sơ nhân sự chính xác trước. Liên kết một nhân viên với tối đa một tài khoản. Thao tác không đổi role hay mật khẩu; phân công bổ sung không thay thế phân công chính hiện có.

DATA_ADMIN dùng Bộ dữ liệu để chọn phòng sở hữu, lưu `app.dataset.owner_department_id`. Dataset mới kế thừa phòng sở hữu của hệ thống nguồn; nếu nguồn chưa có phòng, dùng phòng đang hoạt động theo domain: hr → PNS, academic → PDT, admissions → PTS. Không tự sửa quyền sở hữu dataset cũ. Chọn và lưu từng dataset cũ sau khi kiểm tra nội dung.

Manager cần đồng thời có role phù hợp, nhân viên và phân công đang hoạt động, dataset thuộc đúng domain/phòng. Gold đã xử lý không đồng nghĩa tài khoản đã được cấp quyền.

## Data Catalog

Với DB đã tồn tại trước khi có metadata catalog, chạy:

```sh
make db-catalog-upgrade
```

Lệnh áp dụng migration cộng thêm, có thể chạy lại, không xóa dữ liệu. DB mới đã có bảng từ `infra/postgres/init/02-schema.sql`. Không dùng `docker compose down -v` để sửa lỗi này.

Catalog là metadata mô tả (không tạo bảng nghiệp vụ hoặc chạy pipeline). API `/governance/catalog` dành cho DATA_ADMIN. Nếu thiếu migration, API trả 503 `CATALOG_SCHEMA_NOT_READY` thay cho lỗi 500 chung.

Catalog đọc cả dataset ACTIVE từ `app.dataset` và các bảng/cột đã đăng ký trong `app.catalog_table`, `app.catalog_column`. Metadata chỉnh sửa trong `app.catalog_metadata` được ưu tiên; dấu ẩn vẫn được giữ sau restart. Khi chưa có metadata cột, danh sách cột rỗng; API không suy đoán schema hoặc coi dataset Bronze là đã xuất bản Silver/Gold.
