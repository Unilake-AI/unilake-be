# Nâng cấp DB và sửa khởi tạo pipeline

Chạy từ thư mục gốc repository:

```sh
make db-upgrade
make db-current
```

`db-upgrade` chạy `infra/postgres/migrations/upgrade.psql`: base DDL → identity và Silver → cấu hình/quality/catalog → tự động Silver → Gold → notifications → cấu hình tự chạy mặc định → danh mục tầng dữ liệu. `make dev` chờ PostgreSQL sẵn sàng và nâng cấp DB trước khi bật worker. Điểm nâng cấp này cũng được Docker gọi khi khởi tạo volume mới, qua `04-auto-pipeline.sh`.

Lỗi cũ: init chỉ chạy migration Auto Silver rồi Gold; các bảng `silver_run`, `silver_snapshot`, `silver_head` chưa được tạo nên Gold bị rollback và init dừng, bỏ qua cả notifications và tự động mặc định.

Chuỗi mới có khóa chống hai phiên nâng cấp đồng thời, dừng khi có lỗi, lưu lịch sử từng migration vào `app.schema_migration`, và kiểm tra đủ bảng/hàm/tầng dữ liệu trước khi báo hoàn tất. Migration chạy lại an toàn; bảng lịch sử ghi lần áp dụng đầu tiên và không dùng để bỏ qua việc sửa schema DB cũ chưa có lịch sử. Hai migration nền identity/Silver cũ được bổ sung `IF NOT EXISTS` để hỗ trợ volume đã có một phần bảng.

Migration `20261116_runtime_reference_data.sql` thêm các mã BRONZE/SILVER/GOLD còn thiếu, không đổi ID hoặc cấu hình tầng có sẵn. Không seed lại tài khoản/role khi nâng cấp; không tạo nhân viên/sinh viên/dataset giả; không xóa bảng hoặc dữ liệu nghiệp vụ. Dữ liệu lịch sử đã mất do tạo lại volume cần backup hoặc nguồn gốc để phục hồi, migration không khôi phục file/record bị mất.

Các bảng nghiệp vụ trống sau nâng cấp vẫn có thể là trạng thái đúng khi chưa nhập dữ liệu. Upload file đi qua Bronze/Silver trên MinIO và tạo tổng hợp trong `gold.aggregate`; nó không tự ghi các hồ sơ nguồn vào `app.employee`, `app.student`, `app.course`.

Không dùng `docker compose down -v` để sửa lỗi schema. Với volume hiện có, chạy `make db-upgrade`, sau đó kiểm tra/restart worker nếu cần. Alembic hiện chưa có revision và không thay thế chuỗi SQL này.

Thêm migration mới: tạo SQL cộng thêm, có transaction và chạy lại an toàn; thêm `\ir` cùng dòng ghi lịch sử vào `upgrade.psql` theo thứ tự phụ thuộc. Kiểm thử PostgreSQL cô lập: `MIGRATION_LIVE_TEST=1 pytest -q tests/test_database_migrations.py`.

## Khi cả volume MinIO đã được tạo mới

DB migration không tạo service account MinIO. Nếu file thông tin xác thực local còn nguyên nhưng các tài khoản trên MinIO đều mất, khôi phục đúng tài khoản đã chuẩn bị:

```sh
.venv/bin/python -m app.infra.minio.service_accounts apply
.venv/bin/python -m app.infra.minio.upload_accounts apply
.venv/bin/python -m app.infra.minio.gold_accounts apply
```

Chỉ dùng hai lệnh `apply` đầu khi đã kiểm tra các tài khoản tương ứng chưa tồn tại; chúng không phải lệnh xoay khóa hay cập nhật tài khoản có sẵn. Không chạy lại `prepare`, không dùng credentials quản trị trong runtime API/worker. Các lệnh trên không phục hồi object đã mất.
