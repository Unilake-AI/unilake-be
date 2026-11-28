# Đồng Bộ DB Local Và Chạy Seed

File này gom các lệnh thường dùng để cập nhật PostgreSQL local và chạy seed cho UniLake AI.

Chạy các lệnh từ thư mục gốc project:

```bash
cd /Users/nguyenson/Unilake-BE
```

## 1. Khởi động PostgreSQL local

```bash
docker compose up -d postgres
docker compose ps postgres
```

Kiểm tra DB đã sẵn sàng:

```bash
docker compose exec -T postgres pg_isready -U unilake -d unilake
```

## 2. Cập nhật schema vào DB local

Ưu tiên chạy chuỗi đầy đủ (bao gồm Silver, Gold và danh mục tầng dữ liệu):

```bash
make db-upgrade
make db-current
```

Xem [database-migrations.md](database-migrations.md). Lệnh chỉ chạy `02-schema.sql` bên dưới không đủ để khởi tạo pipeline.


Lệnh này chạy file schema hiện tại vào database `unilake`.

```bash
docker compose exec -T postgres psql \
  -v ON_ERROR_STOP=1 \
  -U unilake \
  -d unilake \
  < infra/postgres/init/02-schema.sql
```

Ghi chú: file schema đang dùng nhiều `CREATE TABLE IF NOT EXISTS`, phù hợp để đồng bộ bảng còn thiếu. Nếu cần thay đổi cấu trúc bảng đã tồn tại, nên tạo migration/Alembic hoặc SQL `ALTER TABLE` riêng.

## 3. Chạy toàn bộ seed theo thứ tự

```bash
docker compose exec -T postgres psql \
  -v ON_ERROR_STOP=1 \
  -U unilake \
  -d unilake \
  < infra/postgres/seed/00-departments.sql

docker compose exec -T postgres psql \
  -v ON_ERROR_STOP=1 \
  -U unilake \
  -d unilake \
  < infra/postgres/seed/01-permissions.sql

docker compose exec -T postgres psql \
  -v ON_ERROR_STOP=1 \
  -U unilake \
  -d unilake \
  < infra/postgres/seed/02-role-permissions.sql

docker compose exec -T postgres psql \
  -v ON_ERROR_STOP=1 \
  -U unilake \
  -d unilake \
  < infra/postgres/seed/03-users-and-roles.sql

docker compose exec -T postgres psql \
  -v ON_ERROR_STOP=1 \
  -U unilake \
  -d unilake \
  < infra/postgres/seed/04-source-systems.sql

docker compose exec -T postgres psql \
  -v ON_ERROR_STOP=1 \
  -U unilake \
  -d unilake \
  < infra/postgres/seed/05-department-datasets.sql
```

## 4. Chạy riêng seed source system

Dùng khi API tạo data source báo `SOURCE_SYSTEM_NOT_FOUND`.

```bash
docker compose exec -T postgres psql \
  -v ON_ERROR_STOP=1 \
  -U unilake \
  -d unilake \
  < infra/postgres/seed/04-source-systems.sql
```

Kiểm tra dữ liệu:

```bash
docker compose exec -T postgres psql \
  -U unilake \
  -d unilake \
  -c "SELECT source_system_id, source_code, source_name, source_type, is_active FROM app.source_system ORDER BY source_system_id;"
```

Kết quả mong đợi:

```text
 source_system_id | source_code |      source_name       | source_type  | is_active
------------------+-------------+------------------------+--------------+-----------
                1 | ADM_SYS     | Admissions System      | FILE_STORAGE | t
                2 | ACA_SYS     | Academic System        | RDBMS        | t
                3 | HR_SYS      | Human Resources System | RDBMS        | t
                4 | FILE_SHARE  | Shared File Storage    | FILE_STORAGE | t
```

### Chạy riêng seed bộ dữ liệu phòng ban

Với PostgreSQL local đã có schema và ba phòng PTS, PDT, PNS đang hoạt động,
chạy riêng phần seed bộ dữ liệu để giữ nguyên cấu hình tài khoản và phân quyền:

```bash
docker compose exec -T postgres sh -c \
  'psql -X -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < infra/postgres/seed/05-department-datasets.sql
```

Lệnh thêm các bộ còn thiếu theo mã. Nếu đã seed ba bộ Nhân sự với mã cũ
`PNS_HO_SO_NHAN_VIEN`, `PNS_PHAN_CONG_CONG_TAC` và `PNS_BANG_CAP`, lệnh
chuyển sang các mã HR tương ứng khi mã mới chưa có và giữ ID bộ dữ liệu.
Tải lại **Nạp dữ liệu** trong không gian Staff để thấy các bộ theo lĩnh vực của tài khoản.

## 5. Lệnh một dòng để đồng bộ schema và seed

```bash
docker compose up -d postgres
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U unilake -d unilake < infra/postgres/init/02-schema.sql
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U unilake -d unilake < infra/postgres/seed/00-departments.sql
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U unilake -d unilake < infra/postgres/seed/01-permissions.sql
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U unilake -d unilake < infra/postgres/seed/02-role-permissions.sql
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U unilake -d unilake < infra/postgres/seed/03-users-and-roles.sql
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U unilake -d unilake < infra/postgres/seed/04-source-systems.sql
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U unilake -d unilake < infra/postgres/seed/05-department-datasets.sql
```

## 6. Reset DB local từ đầu

Chỉ dùng khi muốn xóa sạch dữ liệu local và tạo lại từ init scripts.

```bash
docker compose down -v
docker compose up -d postgres
```

Khi volume PostgreSQL được tạo mới, `infra/postgres/init/03-seed.sh` tự chạy các
file trong `infra/postgres/seed`, bao gồm seed bộ dữ liệu phòng ban.

## 7. Tài khoản seed mặc định

Các user trong seed dùng mật khẩu:

```text
Password@123
```

Một số username có sẵn:

```text
admin
dataadmin
viewer
admissions_manager
admissions_staff
academic_manager
academic_staff
hr_manager
hr_staff
```
