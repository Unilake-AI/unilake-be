# UniLake Backend

**Dịch vụ backend cho nền tảng tích hợp và xử lý dữ liệu UniLake AI.**

UniLake Backend cung cấp API quản lý nguồn dữ liệu, xác thực người dùng và điều phối tác vụ thu thập dữ liệu. Hệ thống tổ chức dữ liệu theo kiến trúc Bronze–Silver–Gold, sử dụng PostgreSQL để quản lý metadata và hàng đợi tác vụ, MinIO để lưu trữ đối tượng, cùng DuckDB để xử lý dữ liệu.

Repository này bao gồm API FastAPI, ingestion worker và pipeline chuẩn hóa dữ liệu. Frontend Next.js được quản lý riêng.

## Mục lục

- [Phạm vi chức năng](#phạm-vi-chức-năng)
- [Kiến trúc và công nghệ](#kiến-trúc-và-công-nghệ)
- [Cài đặt và khởi chạy](#cài-đặt-và-khởi-chạy)
- [Giao diện API](#giao-diện-api)
- [Quản lý cơ sở dữ liệu](#quản-lý-cơ-sở-dữ-liệu)
- [Kiểm thử và chất lượng mã nguồn](#kiểm-thử-và-chất-lượng-mã-nguồn)
- [Cấu trúc dự án](#cấu-trúc-dự-án)
- [Tài liệu tham khảo](#tài-liệu-tham-khảo)

## Phạm vi chức năng

| Phân hệ | Chức năng | Trạng thái |
| --- | --- | --- |
| Xác thực | Đăng nhập, cấp lại token, đăng xuất, quản lý và thu hồi phiên | Đã triển khai |
| Nguồn dữ liệu | Quản lý danh mục nguồn; đọc CSV, Excel và PostgreSQL | Đã triển khai |
| Ingestion | Tạo và theo dõi tác vụ; xử lý bất đồng bộ; lưu Bronze; xác minh phiên bản và checksum | Đã triển khai |
| Chuẩn hóa và Silver | Làm sạch, chuẩn hóa, cách ly bản ghi lỗi và xuất bản Silver | API thủ công giới hạn ở ba profile `DEMO_FLOW_*` phục vụ demo |
| Quản trị dữ liệu | Tra cứu danh mục dữ liệu | Đã có API đọc catalog |
| Truy vấn ngôn ngữ tự nhiên (NLQ) | Tiếp nhận câu hỏi và xử lý luồng truy vấn | Đang sử dụng LLM mô phỏng và bộ thực thi trả kết quả rỗng |
| Dashboard, lịch sử truy vấn, quản lý người dùng | Cấu trúc module | Chưa có endpoint nghiệp vụ |

Schema Silver đang ở giai đoạn dự thảo; bộ quy tắc nghiệp vụ và phê duyệt sử dụng trong môi trường production chưa hoàn tất. NLQ hiện được cấu hình cố định với `MockLLMClient` và `EmptyGoldQueryExecutor` trong `app/providers.py`; thay đổi `LLM_PROVIDER` chưa kích hoạt kết nối LLM thực tế.

## Kiến trúc và công nghệ

```text
Nguồn dữ liệu: CSV / Excel / PostgreSQL
                    │
                    ▼
              Ingestion API
                    │
                    ▼
           Hàng đợi PostgreSQL
                    │
                    ▼
             Ingestion Worker
                    │
                    ▼
              MinIO · Bronze
                    │
           Gọi pipeline riêng
                    │
                    ▼
       Làm sạch và chuẩn hóa dữ liệu
                    │
                    ▼
            Xuất bản lớp Silver
```

API ingestion trả HTTP `202 Accepted` sau khi lưu tác vụ vào PostgreSQL. Worker tiếp nhận tác vụ và xử lý độc lập với tiến trình API. Phạm vi mỗi tác vụ ingestion là **Source → Bronze**; pipeline Bronze → Silver được kích hoạt riêng. Luồng tự động đến Gold chưa được hoàn thiện.

| Thành phần | Công nghệ |
| --- | --- |
| Ngôn ngữ và API | Python 3.11+, FastAPI, Pydantic |
| Cơ sở dữ liệu và truy cập dữ liệu | PostgreSQL 16, SQLAlchemy, asyncpg |
| Lưu trữ đối tượng | MinIO, giao thức tương thích S3 |
| Xử lý dữ liệu | DuckDB, PyArrow, Delta Lake |
| Xác thực | JWT, Argon2 |
| Môi trường phát triển | Docker Compose |
| Kiểm thử và phân tích mã | pytest, Ruff, mypy, Pyright |

Docker Compose có cấu hình Redis. Hàng đợi ingestion hiện sử dụng PostgreSQL.

## Cài đặt và khởi chạy

### Yêu cầu môi trường

- Python **3.11 trở lên**.
- Docker và Docker Compose **v2.24 trở lên**, hỗ trợ thuộc tính `env_file.required`.
- Các cổng mặc định còn khả dụng: `8000`, `5434`, `9000`, `9001`, `6379`.

Thực hiện các lệnh sau tại thư mục gốc repository. Hướng dẫn kích hoạt môi trường ảo bên dưới áp dụng cho shell Bash/Zsh.

### 1. Cài đặt thư viện

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

### 2. Thiết lập cấu hình

Cập nhật `.env` trước khi khởi động dịch vụ:

| Biến môi trường | Mô tả |
| --- | --- |
| `DB_USER`, `DB_PASSWORD`, `DB_NAME` | Thông tin kết nối cơ sở dữ liệu ứng dụng |
| `DB_PORT` | Cổng PostgreSQL trên máy host; mặc định trong file mẫu là `5434` |
| `DB_READONLY_USER`, `DB_READONLY_PASSWORD` | Tài khoản chỉ đọc schema Gold |
| `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` | Tài khoản quản trị MinIO phục vụ khởi tạo và cấp tài khoản dịch vụ; mật khẩu tối thiểu 8 ký tự |
| `JWT_SECRET_KEY` | Khóa ký token; tạo bằng `openssl rand -hex 32` |
| `CORS_ORIGINS` | Danh sách origin frontend được phép truy cập, phân cách bằng dấu phẩy |

Tham khảo [`.env.example`](.env.example) để cấu hình bucket, DuckDB và thời hạn token. Lưu thông tin xác thực trong `.env` hoặc `.env*.local` và loại các file này khỏi quản lý phiên bản.

### 3. Khởi động hạ tầng

```bash
docker compose up -d postgres minio minio-init redis
docker compose ps -a
```

Chỉ tiếp tục khi PostgreSQL và MinIO có trạng thái `healthy`, đồng thời `minio-init` kết thúc với mã `0`. Dịch vụ khởi tạo tạo các bucket Bronze, Silver, Gold và bật lưu phiên bản cho Bronze.

Script khởi tạo PostgreSQL chỉ chạy khi tạo volume dữ liệu mới. Với database đã tồn tại, thực hiện theo phần [Quản lý cơ sở dữ liệu](#quản-lý-cơ-sở-dữ-liệu).

### 4. Cấp tài khoản dịch vụ MinIO

Trong môi trường Python đã kích hoạt, thực hiện lần lượt:

```bash
python -m app.infra.minio.service_accounts prepare
python -m app.infra.minio.service_accounts apply
python -m app.infra.minio.service_accounts export-runtime
```

Các bước này tạo file thông tin xác thực `.env.bronze-accounts.local`, cấp tài khoản Bronze/Silver trên MinIO và xuất cấu hình theo dịch vụ:

| File cấu hình | Quyền sử dụng |
| --- | --- |
| `.env.bronze-backend.local` | Backend đọc Bronze và nguồn MinIO đã cấu hình |
| `.env.bronze-worker.local` | Worker đọc nguồn, ghi Bronze và đọc lại để xác minh |
| `.env.silver-backend.local` | Backend ghi Silver |

Quy trình trên áp dụng cho môi trường mới. Nếu tài khoản hoặc file cấu hình đã tồn tại, tham khảo [quản lý service account](docs/bronze-service-accounts.md) trước khi thực hiện lại. Runtime yêu cầu tài khoản dịch vụ tương ứng và không tự chuyển sang sử dụng tài khoản quản trị.

Nguồn dữ liệu thuộc bucket S3/MinIO riêng cần tài khoản `source-reader`. Cấp tài khoản này trước khi xuất lại cấu hình runtime theo [hướng dẫn đọc nguồn](docs/ingestion-source-readers.md) và [phân quyền MinIO](docs/bronze-service-accounts.md).

### 5. Khởi chạy ứng dụng

#### Sử dụng Docker Compose

```bash
docker compose up -d --build backend ingestion-worker
```

Theo dõi nhật ký dịch vụ:

```bash
docker compose logs -f backend ingestion-worker
```

Compose tự nạp cấu hình tài khoản theo dịch vụ và kết nối hạ tầng qua hostname nội bộ `postgres`, `minio`. Cấu hình hiện tại phục vụ phát triển, có mount mã nguồn và bật tự động tải lại API bằng `--reload`.

#### Sử dụng Python trên máy phát triển

Thiết lập `DB_HOST=localhost`, `MINIO_ENDPOINT=localhost:9000` và `DB_PORT` khớp cổng PostgreSQL trên host. Kích hoạt `.venv` trong mỗi terminal trước khi chạy.

Terminal thứ nhất — API:

```bash
uvicorn app.main:app --reload --port 8000
```

Terminal thứ hai — worker:

```bash
python -m app.ingestion_worker
```

Khi chạy từ thư mục gốc, ứng dụng tự đọc lần lượt `.env`, `.env.bronze-accounts.local`, `.env.silver-backend.local` và `.env.source-reader.local`. Biến môi trường của tiến trình có độ ưu tiên cao hơn cấu hình trong file. Môi trường Python local đọc file tài khoản tổng; Docker Compose nạp các file runtime theo dịch vụ.

Đường dẫn nguồn file phải tương ứng với môi trường worker: `/data/...` trong container ánh xạ tới thư mục `data/` trên host; worker Python local sử dụng đường dẫn thực trên máy.

### 6. Xác nhận dịch vụ

```bash
curl http://localhost:8000/health
```

Phản hồi dự kiến:

```json
{"status": "ok"}
```

Endpoint `/health` xác nhận tiến trình API đang phản hồi; endpoint này chưa kiểm tra kết nối PostgreSQL hoặc MinIO.

| Dịch vụ | Địa chỉ mặc định |
| --- | --- |
| Swagger UI | [http://localhost:8000/docs](http://localhost:8000/docs) |
| ReDoc | [http://localhost:8000/redoc](http://localhost:8000/redoc) |
| MinIO Console | [http://localhost:9001](http://localhost:9001) |
| MinIO S3 API | `localhost:9000` |
| PostgreSQL | `localhost:5434` |
| Redis | `localhost:6379` |

Frontend sử dụng API base URL `http://localhost:8000/api/v1` với cấu hình mặc định.

## Giao diện API

Các endpoint dưới đây sử dụng prefix mặc định `/api/v1`. Swagger UI cung cấp chi tiết về dữ liệu đầu vào, phản hồi và yêu cầu xác thực.

| Phân hệ | Phương thức | Đường dẫn |
| --- | --- | --- |
| Xác thực | `POST` | `/api/v1/auth/login` |
| Cấp lại token | `POST` | `/api/v1/auth/refresh` |
| Đăng xuất | `POST` | `/api/v1/auth/logout` |
| Danh sách phiên | `GET` | `/api/v1/auth/sessions` |
| Danh sách nguồn dữ liệu | `GET` | `/api/v1/datasources` |
| Tạo nguồn dữ liệu | `POST` | `/api/v1/datasources/create` |
| Tạo tác vụ ingestion | `POST` | `/api/v1/ingestion/jobs` |
| Danh sách tác vụ | `GET` | `/api/v1/ingestion/jobs` |
| Chi tiết tác vụ | `GET` | `/api/v1/ingestion/jobs/{job_id}` |
| Chuẩn hóa dữ liệu demo | `POST` | `/api/v1/pipeline/cleaning/ingestion-jobs/{ingestion_job_id}` |
| Danh mục dữ liệu | `GET` | `/api/v1/governance/catalog` |
| NLQ mô phỏng | `POST` | `/api/v1/nlq/ask` |

Tạo tác vụ ingestion và chạy cleaning yêu cầu vai trò `ADMIN` hoặc `DATA_ADMIN`. Nguồn và dataset phải được cấu hình hợp lệ trong catalog trước khi tạo tác vụ.

Tham khảo [đặc tả ingestion](docs/ingestion-execution.md) để tra cứu payload, vòng đời tác vụ và mã lỗi. Quy trình Bronze → Silver với dữ liệu tổng hợp được mô tả trong [hướng dẫn demo chuẩn hóa](docs/standardization/manual-live-demo.md).

## Quản lý cơ sở dữ liệu

DDL, dữ liệu khởi tạo và SQL migration được quản lý trong `infra/postgres/`. Schema dữ liệu nằm tại `docs/data-schema/`.

| Trường hợp | Hướng xử lý |
| --- | --- |
| Khởi tạo database mới | Compose chạy init script khi tạo volume PostgreSQL |
| Cập nhật database hiện có | Áp dụng thay đổi schema và seed theo [tài liệu database](docs/database.md) và [hướng dẫn đồng bộ local](docs/db-local-sync.md) |
| Sử dụng Alembic | Đã có cấu hình nhưng chưa có revision; `alembic upgrade head` chưa thay thế DDL và SQL migration hiện tại |

Giữ nguyên volume PostgreSQL khi cập nhật schema để bảo toàn dữ liệu.

## Kiểm thử và chất lượng mã nguồn

Chạy các bước kiểm tra sau trong môi trường đã cài nhóm thư viện `dev`:

```bash
pytest
ruff check app tests
ruff format app tests --check
mypy --config-file pyproject.toml app
pyright --pythonpath "$(command -v python)"
```

Pytest đo độ bao phủ mã nguồn trong `app` với ngưỡng tối thiểu **70%**. Các kiểm thử tích hợp với dịch vụ thực tế yêu cầu hạ tầng, thông tin xác thực và dữ liệu kiểm thử tương ứng. Những biến như `BRONZE_LIVE_TEST=1`, `BRONZE_IAM_LIVE_TEST=1` và `INGESTION_LIVE_TEST=1` dùng để bật từng nhóm live test; xem điều kiện trong `tests/*live*.py` trước khi chạy.

Có thể chạy bản xem trước chuẩn hóa bằng dữ liệu kiểm thử:

```bash
python scripts/standardization/preview_s2.py --case admissions-flat
```

| Lệnh | Chức năng |
| --- | --- |
| `make run` | Khởi chạy API ở chế độ phát triển |
| `make worker` | Khởi chạy ingestion worker |
| `make test` | Chạy pytest |
| `make lint` | Kiểm tra mã nguồn bằng Ruff |

Quy trình CI được định nghĩa tại [`.github/workflows/ci-backend.yml`](.github/workflows/ci-backend.yml). Bộ kiểm thử mặc định không xác nhận kết nối tới PostgreSQL và MinIO thực tế.

## Tái tạo dữ liệu mẫu

```bash
python data/synthetic/generate_samples.py
```

Lệnh sinh dữ liệu giả lập xác định cho HR, Admissions và Academic, xuất CSV theo
schema hiện tại trong `docs/data-schema/`. Có thể truyền thư mục đích để kiểm tra
riêng: `python data/synthetic/generate_samples.py /tmp/unilake-samples`.
Các script `generate_hr.py`, `generate_admissions.py`, `generate_academic.py` khi
chạy không có tham số cũng gọi quy trình này. Dữ liệu trung gian theo cấu trúc cũ
chỉ được tạo trong thư mục tạm. `enrollment` hiện là đăng ký lớp ở Academic;
liên kết nhập học được lưu bằng `student.admission_id`.

## Cấu trúc dự án

```text
app/
├── core/                 # Cấu hình, bảo mật, dependency và xử lý lỗi
├── domains/              # Đăng ký và định nghĩa miền dữ liệu
├── infra/                # Adapter PostgreSQL, MinIO, DuckDB và LLM
├── modules/              # Các phân hệ nghiệp vụ và API
├── repositories/         # Lớp truy cập dữ liệu
├── main.py               # Điểm khởi chạy FastAPI
└── ingestion_worker.py   # Điểm khởi chạy worker
alembic/                  # Cấu hình Alembic
infra/postgres/           # DDL, seed và SQL migration
scripts/standardization/  # Công cụ xem trước, chạy thử và demo
data/                     # Dữ liệu mẫu, bộ sinh dữ liệu và fixture
docs/                     # Kiến trúc, schema và hướng dẫn kỹ thuật
tests/                    # Kiểm thử đơn vị, contract và tích hợp
```

Schema và profile trong `docs/` và `data/standardization/` là tài nguyên cần thiết khi chạy ứng dụng. Giữ nguyên các đường dẫn này khi triển khai. Docker image đã bao gồm tài nguyên runtime; gói wheel hiện chưa chứa tài nguyên ngoài `app/`. Các hình thức chạy được hỗ trợ hiện tại là source checkout và Docker image.

## Tài liệu tham khảo

| Chủ đề | Tài liệu |
| --- | --- |
| Kiến trúc | [Kiến trúc xử lý dữ liệu](docs/architecture/data-processing-architecture.md) |
| Cơ sở dữ liệu | [Database và schema](docs/database.md) · [Đồng bộ môi trường local](docs/db-local-sync.md) |
| Ingestion | [Luồng thực thi](docs/ingestion-execution.md) · [Reader dữ liệu nguồn](docs/ingestion-source-readers.md) |
| Phân quyền lưu trữ | [Service account MinIO](docs/bronze-service-accounts.md) |
| Chất lượng dữ liệu | [Chính sách kiểm tra đầu vào Bronze](docs/bronze-input-validation-policy.md) |
| Chuẩn hóa | [Tổng quan và contract](docs/standardization/README.md) |
| Mở rộng hệ thống | [Hướng dẫn mở rộng domain](docs/domain-extension-guide.md) |

Một số tài liệu kế thừa sử dụng cấu trúc monorepo với đường dẫn `backend/` hoặc `PYTHONPATH=backend`. Khi áp dụng cho repository này, chạy từ thư mục gốc và điều chỉnh các tiền tố đường dẫn tương ứng.
