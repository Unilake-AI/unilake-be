# Deploy backend lên Render

## Blueprint cho Render Postgres

Repository có `render.yaml` để tạo `unilake-api` và `unilake-db` cùng region
Singapore, PostgreSQL 16. Cấu hình mẫu dùng gói free phục vụ demo; kiểm tra giới
hạn và thời hạn của gói trên Render trước khi tạo. Chọn gói phù hợp nếu cần
chạy lâu dài hoặc xử lý dữ liệu lớn.

1. Push mã nguồn và `render.yaml` lên Git repository.
2. Trong Render chọn **New → Blueprint**, kết nối repository và chọn branch.
3. Nhập `CORS_ORIGINS` là origin của frontend, ví dụ `https://your-app.vercel.app`.
4. Kiểm tra tài nguyên và plan hiển thị trước khi tạo Blueprint.
5. Khởi tạo schema, migration và seed bằng các lệnh ở phần bên dưới.

Blueprint tự nối các biến `DB_*` với database qua kết nối internal và sinh khóa
JWT; không cần chép mật khẩu database vào source code. Schema và tài khoản chưa
được tự tạo khi deploy. Nếu đã có Render Postgres, cấu hình Web Service thủ công
theo bảng bên dưới thay vì tạo thêm database bằng Blueprint.

Trong database Dashboard, mục **Connect** cung cấp kết nối external để chạy
`psql` từ máy local. API dùng kết nối internal; không đặt external hostname vào
`DB_HOST` của API nếu dùng Blueprint. Không chạy `01-init.sh` của Docker Compose
trên Render: script đó còn tạo role và database nguồn phục vụ môi trường local.

## Continuous deployment (CD)

`render.yaml` theo dõi nhánh `develop` và đặt `autoDeployTrigger: checksPass`.
Luồng deploy sau khi Blueprint được tạo:

```text
Push / merge vào develop
  → GitHub Actions: lint, format, mypy, pyright, SQL migration, seed, pytest
  → Tất cả CI checks thành công
  → Render build Docker image và deploy
  → Render kiểm tra /health trước khi chuyển traffic
```

CI vẫn kiểm tra pull request và push vào `main`, `develop`; service Render chỉ
deploy từ `develop`. Push vào `main` không kích hoạt deploy của service này.
CI thất bại sẽ chặn auto-deploy. Seed trong CI chạy trên PostgreSQL tạm của
GitHub Actions, không chạm vào database Render.

Sau khi push thay đổi cấu hình, sync Blueprint trên Render. Trong service Settings,
kiểm tra branch là `develop`, Auto-Deploy là **After CI Checks Pass**, Health Check
Path là `/health`. Repository cần được kết nối qua GitHub integration để Render
nhận CI checks. Không cần `RENDER_DEPLOY_HOOK_URL` hoặc Render API key.

Nếu tạo Web Service thủ công, đặt ba mục này trong Dashboard. Theo dõi kết quả CI
ở GitHub Actions và kết quả build/deploy ở Render Events/Deploys. Việc build hoặc
health check thất bại được báo ở Render; CI pass chưa đồng nghĩa deploy thành công.

Schema và seed production vẫn áp dụng thủ công theo hướng dẫn dưới đây; CD không
tự chạy các file SQL trên database production hoặc đặt lại tài khoản sau deploy.

Tham khảo [Render: tích hợp CI](https://render.com/docs/deploys#integrating-with-ci).

## Cấu hình Web Service thủ công

Tạo Web Service từ repository, chọn runtime **Docker**, Dockerfile `./Dockerfile`.
Giữ Docker Command mặc định và đặt Health Check Path là `/health`.
Image chạy Uvicorn trên `0.0.0.0`, đọc `PORT` do Render cung cấp.
Render không tự chạy các service trong Docker Compose.

Thêm các biến môi trường trong Render Dashboard:

| Biến | Giá trị |
| --- | --- |
| `ENVIRONMENT` | `production` |
| `DEBUG` | `false` |
| `DB_HOST` | Host database đích, không phải `localhost` hoặc `postgres` của Compose |
| `DB_PORT` | `5432` hoặc cổng nhà cung cấp database |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Thông tin database đích |
| `DB_ECHO` | `false` |
| `JWT_SECRET_KEY` | Tạo bằng `openssl rand -hex 32` |
| `CORS_ORIGINS` | Origin HTTPS của frontend, nhiều origin cách nhau bằng dấu phẩy |

Code hiện đọc `DB_*`; chỉ đặt `DATABASE_URL` chưa đủ để kết nối.
Với Render Postgres cùng tài khoản và region, dùng thông tin kết nối internal.
Database bên ngoài cần kiểm tra yêu cầu TLS và quyền truy cập từ Render.
Không commit `.env` hoặc thông tin xác thực vào repository.

## Khởi tạo database và 9 tài khoản

Tài khoản được tạo ở database local không tự xuất hiện trên database deploy.

Với **database mới**, từ máy có `psql`, đặt `PGHOST`, `PGPORT`, `PGDATABASE`,
`PGUSER`, `PGPASSWORD` theo kết nối external của database đích; đặt `PGSSLMODE`
theo yêu cầu nhà cung cấp. Từ thư mục gốc repository, chạy:

```bash
psql -v ON_ERROR_STOP=1 --single-transaction -f infra/postgres/init/02-schema.sql
```

Tiếp theo áp dụng các file `infra/postgres/migrations/*.sql` theo tên tăng dần,
mỗi file bằng `psql -v ON_ERROR_STOP=1 --single-transaction -f <file>`.
Chỉ tiếp tục khi từng file chạy thành công. Với database đã tồn tại, kiểm tra
migration nào đã áp dụng trước khi chạy. Repo chưa có revision trong
`alembic/versions`; `alembic upgrade head` không thay thế bước khởi tạo này.

Để tạo **tài khoản demo** theo 9 role, chạy tiếp trên database đích:

```bash
psql -v ON_ERROR_STOP=1 --single-transaction \
  -f infra/postgres/seed/01-permissions.sql \
  -f infra/postgres/seed/02-role-permissions.sql \
  -f infra/postgres/seed/03-users-and-roles.sql
```

Username ở README, mật khẩu khởi tạo `Password@123`. Đổi mật khẩu bằng công cụ
quản trị trước khi dùng trên hệ thống public; backend chưa có endpoint đổi mật khẩu.
Không đặt seed tài khoản trong startup: seed gán lại role cho tài khoản mẫu và
khóa hai tài khoản cũ `analyst`, `manager`.

## Ingestion và lưu trữ

Đăng nhập cần PostgreSQL đã khởi tạo. Để ingestion chạy hoàn chỉnh cần thêm:

- Background Worker chạy `python -m app.ingestion_worker`, dùng chung PostgreSQL.
- MinIO/S3 tương thích, truy cập được từ Render, với endpoint, TLS, bucket và
  tài khoản dịch vụ theo `.env.example` và `docs/bronze-service-accounts.md`.
- Các biến tài khoản đọc Bronze/ghi Silver cho API, ghi Bronze/xác minh cho worker.

File `.env*.local` trên máy không được đưa vào Docker image; đặt biến trực tiếp
trong môi trường từng service. Không dùng filesystem tạm của web service để giữ
dữ liệu qua các lần deploy. API không tự xử lý ingestion job nếu chưa có worker.

## Kiểm tra sau deploy

1. `GET /health` trả `{"status":"ok"}`; endpoint này chỉ kiểm tra tiến trình API.
2. Mở `/docs`, đăng nhập bằng tài khoản trên database đích để kiểm tra kết nối DB.
3. Gọi API từ frontend để kiểm tra CORS.
4. Nếu dùng ingestion, xác nhận worker xử lý job và lưu Bronze thành công.

Giới hạn dữ liệu theo phòng ban và kiểm tra đầy đủ bảng `role_permission` chưa
được triển khai; xem phạm vi chức năng trong README trước khi mở dữ liệu thật.

Tài liệu Render: [Web Services](https://render.com/docs/web-services),
[Postgres connections](https://render.com/docs/postgresql-creating-connecting).
