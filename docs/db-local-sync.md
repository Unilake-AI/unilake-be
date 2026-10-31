# Đồng Bộ DB Local Và Chạy Seed

File này gom các lệnh thường dùng để cập nhật PostgreSQL local và chạy seed cho UniLake AI.

Chạy các lệnh từ thư mục gốc project:

```bash
cd /Users/nguyenson/UniDataLake_AI
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

## 5. Lệnh một dòng để đồng bộ schema và seed

```bash
docker compose up -d postgres
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U unilake -d unilake < infra/postgres/init/02-schema.sql
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U unilake -d unilake < infra/postgres/seed/01-permissions.sql
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U unilake -d unilake < infra/postgres/seed/02-role-permissions.sql
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U unilake -d unilake < infra/postgres/seed/03-users-and-roles.sql
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U unilake -d unilake < infra/postgres/seed/04-source-systems.sql
```

## 6. Reset DB local từ đầu

Chỉ dùng khi muốn xóa sạch dữ liệu local và tạo lại từ init scripts.

```bash
docker compose down -v
docker compose up -d postgres
```

Sau khi reset, Docker sẽ tự chạy các file trong `infra/postgres/init` lúc volume Postgres được tạo mới. Các file trong `infra/postgres/seed` không tự chạy, nên vẫn cần chạy seed ở bước 3 nếu muốn có user/role/source system mẫu.

## 7. Tài khoản seed mặc định

Các user trong seed dùng mật khẩu:

```text
Password@123
```

Một số username có sẵn:

```text
admin
dataadmin
analyst
manager
viewer
```
