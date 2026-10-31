# Tech Stack & Setup Roadmap – UniLake AI V1.0

> **Mục đích:** một file duy nhất để team biết *cần những gì*, *setup theo thứ tự nào*, *cấu hình ra sao* và *thế nào là xong* cho DB, Data Lake, Backend, Frontend.
> **Nguồn chuẩn:** proposal `docs/user-study/C1SE_11_ProjectProposal_ver1 - review.docx` (mục 7.3 NFR, 10 Tech Stack, 11.3 Sprint Plan) và [system-architecture-baseline.md](architecture/system-architecture-baseline.md). Khi mâu thuẫn, proposal thắng.
> **Trạng thái:** hướng dẫn để tự setup. Các file config bên dưới là **mẫu đề xuất**, chưa được thêm vào repo.

---

## Mục lục

1. [Tổng quan tech stack](#1-tổng-quan-tech-stack)
2. [Roadmap 12 tuần theo hạ tầng](#2-roadmap-12-tuần-theo-hạ-tầng)
3. [Kiến trúc hạ tầng dev](#3-kiến-trúc-hạ-tầng-dev)
4. [Bước 0 – Công cụ trên máy](#4-bước-0--công-cụ-trên-máy)
5. [Bước 1 – Environment configuration (.env)](#5-bước-1--environment-configuration-env)
6. [Bước 2 – PostgreSQL](#6-bước-2--postgresql)
7. [Bước 3 – MinIO (Object Storage)](#7-bước-3--minio-object-storage)
8. [Bước 4 – Cấu trúc Bronze / Silver / Gold](#8-bước-4--cấu-trúc-bronze--silver--gold)
9. [Bước 5 – DuckDB + Delta Lake](#9-bước-5--duckdb--delta-lake)
10. [Bước 6 – docker-compose.yml hoàn chỉnh](#10-bước-6--docker-composeyml-hoàn-chỉnh)
11. [Bước 7 – Kiểm tra đọc/ghi và giao tiếp service (smoke test)](#11-bước-7--kiểm-tra-đọcghi-và-giao-tiếp-service-smoke-test)
12. [Bước 8 – Backend (FastAPI)](#12-bước-8--backend-fastapi)
13. [Bước 9 – Frontend (Next.js)](#13-bước-9--frontend-nextjs)
14. [Bước 10 – CI/CD và chất lượng](#14-bước-10--cicd-và-chất-lượng)
15. [Definition of Done cho task hạ tầng W2](#15-definition-of-done-cho-task-hạ-tầng-w2)
16. [Việc team cần chốt](#16-việc-team-cần-chốt)
17. [Troubleshooting (Windows)](#17-troubleshooting-windows)

---

## 1. Tổng quan tech stack

### 1.1 Core stack (proposal mục 10.1)

| Layer | Công nghệ | Phiên bản đề xuất | Vai trò | Trong repo | Tuần bắt đầu dùng |
|---|---|---|---|---|---|
| Database | **PostgreSQL** | 16 (image `postgres:16-alpine`) | Schema `app` (state, auth, catalog, lineage, DQ) + schema `gold` (data mart phục vụ query) | Driver + Alembic có, **container chưa có** | W2 |
| Object Storage | **MinIO** (S3 API) | Pin 1 tag `RELEASE.*` cụ thể | Raw data Bronze + file Delta của Silver/Gold | Có `infra/minio` (chỉ settings) | W2 |
| Data Format | **Delta Lake** (`deltalake` = delta-rs) | `deltalake>=1.0` | Bảng versioned ở Silver/Gold trên MinIO | **Chưa có** | W4 |
| Processing / Query | **DuckDB** | `duckdb>=1.2` + extension `httpfs`, `delta`, `postgres`, `excel` | Đọc nguồn, làm sạch, tổng hợp, ghi Gold sang PostgreSQL | Có trong deps | W2–W5 |
| Backend | **Python 3.11+ / FastAPI** | FastAPI ≥0.115, SQLAlchemy 2 async, Alembic | REST API `/api/v1`, modular monolith | Có | W2 (khung), W8 (API) |
| Auth | **JWT / OAuth2** (password flow) | `pyjwt`, `pwdlib[argon2]` | Đăng nhập, RBAC | Có `core/security.py` | W8 |
| Data Quality | **Great Expectations** | `great-expectations>=1.2` | Rule DQ theo dataset/layer, mục tiêu ≥90% pass (NFR03) | Có trong deps | W5 |
| Metadata | **OpenMetadata** (tùy chọn) | — | Catalog/Lineage UI. V1.0 lưu catalog ở PostgreSQL (D7) | Không | Tùy chọn sau W5 |
| AI | **LLM API** | OpenAI / Anthropic qua `LLM_PROVIDER` | Text-to-SQL | Có `infra/llm` (mock) | W6 |
| AI Framework | **LangChain** | `langchain>=1.x` + `langchain-openai` hoặc `langchain-anthropic` | Prompt, gọi LLM | Có trong deps | W6 |
| SQL Validation | `sqlglot` | ≥25 | Parse SQL, chỉ cho `SELECT`, kiểm tra bảng | **Chưa có** | W7 |
| Frontend | **Next.js** (App Router) + React + TypeScript | Next 15, React 19, TS 5 | Web Dashboard, Chatbot NLQ, Governance View | Có | W2 (khung), W9 (UI) |
| UI | **TailwindCSS** | 3.4 (đang dùng) | Styling | Có | W9 |
| Visualization | **Recharts** (hoặc Chart.js) | Recharts 2.x | Biểu đồ KPI | **Chưa có** | W9 |
| Container | **Docker / Docker Compose** | Docker Desktop, Compose v2 | Chạy hạ tầng dev, deploy (NFR09) | `docker-compose.yml` **rỗng** | W2 |
| CI/CD | **GitHub Actions** | — | Lint, type-check, test | Có 5 workflow | Có sẵn |
| API Testing | Swagger (`/docs`) / Postman | — | Test API, NFR10 | Swagger có sẵn | W8 |
| Quản lý | Jira, Figma, draw.io | — | Tracking, UI/UX, sơ đồ | Ngoài repo | — |

### 1.2 Công nghệ hỗ trợ ingestion đa nguồn (proposal mục 10.2)

Nên đặt thành **optional dependency group** để máy nào không cần OCR thì không phải cài Tesseract.

| Nhóm | Thư viện | Dùng khi |
|---|---|---|
| Structured / semi-structured | DuckDB (`read_csv`, `read_xlsx` qua extension `excel`, `postgres_scan`, `mysql_scan`), `openpyxl` | CSV, Excel, bảng từ DB nguồn |
| Tài liệu có sẵn text | `pdfplumber`, `python-docx` | PDF text, Word |
| Nhị phân | Lưu file gốc lên MinIO (khuyến nghị), metadata ở PostgreSQL. `BYTEA`/`BLOB` chỉ cho file nhỏ | Ảnh, tệp đính kèm |
| Scan / ảnh | `Pillow`, `pytesseract` (cần cài Tesseract binary), Claude Vision API cho case khó | PDF scan, ảnh |

> **Khuyến nghị:** file nhị phân lớn nên nằm ở MinIO (Bronze), không nhét vào PostgreSQL. Proposal có nêu `BYTEA`/`BLOB`; dùng cho ảnh nhỏ thì được, nhưng object storage là chuẩn của Data Lake.

### 1.3 Không dùng ở V1.0

| Công nghệ | Lý do |
|---|---|
| Redis, Celery, ARQ | D6: job nền chạy trong process (FastAPI `BackgroundTasks` / APScheduler), trạng thái ở PostgreSQL. `SETUP.md` đang nhắc Redis nhưng không cần. |
| `ortools` | Constraint Optimization / What-If nằm ngoài phạm vi V1.0. Đang có trong `backend/pyproject.toml`, nên gỡ. |
| Spark, Airflow, Kafka | Quá nặng cho quy mô dữ liệu và 12 tuần; DuckDB đủ. |
| VectorDB | Schema retrieval V1.0 lấy từ Catalog trong PostgreSQL. |

---

## 2. Roadmap 12 tuần theo hạ tầng

Bám đúng sprint plan của proposal (mục 11.3). Cột "Hạ tầng / stack cần sẵn sàng" là thứ phải xong **trước hoặc trong** tuần đó.

| Tuần | Hoạt động (proposal) | Hạ tầng / stack cần sẵn sàng | Kết quả kiểm chứng |
|---|---|---|---|
| **W2** | Dataset Design, **Infrastructure Setup** | PostgreSQL + MinIO chạy bằng compose; bucket Bronze/Silver/Gold; DuckDB đọc/ghi MinIO + PostgreSQL; `.env` chuẩn; smoke test pass | `python infra/scripts/smoke_test.py` in toàn `OK` (mục 11) |
| W3 | Data Ingestion | Ghi raw vào `bronze/`; bảng `app.ingestion_job`; `datasources` lưu cấu hình nguồn | Upload CSV mẫu (`data/samples/*`) → có object trong `bronze/` + job `succeeded` |
| W4 | Cleaning & Standardization | `deltalake` ghi Delta vào `silver/`; DuckDB đọc `delta_scan` | Bảng Silver có version Delta, đúng kiểu dữ liệu |
| W5 | Gold Layer & Governance | Schema `gold` trong PostgreSQL; role `unilake_reader`; Great Expectations; bảng catalog/lineage ở `app` | Gold có dữ liệu; DQ pass rate ≥90%; lineage Bronze→Silver→Gold |
| W6 | Text-to-SQL Baseline | LangChain + LLM key thật trong `.env` (không commit) | NLQ sinh SQL trên schema Gold |
| W7 | SQL Validation & Evaluation | `sqlglot`; kết nối đọc Gold bằng `unilake_reader` + `statement_timeout` | Bộ test Text-to-SQL, accuracy ≥85% |
| W8 | FastAPI Backend | JWT/RBAC, OpenAPI export vào `docs/api/` | Swagger đầy đủ endpoint |
| W9 | Next.js Dashboard | Typed API client từ OpenAPI, Recharts, TanStack Query | Dashboard + Chatbot gọi API thật |
| W10 | System Integration | Compose profile `app` (backend + frontend trong container); CI chạy integration test với service container | `docker compose --profile app up` chạy toàn hệ thống |
| W11 | Testing & Evaluation | Playwright e2e, đo query <5 giây (NFR01) | Test report |
| W12 | Bug fix, Docs, Demo | Seed dữ liệu demo, script reset | Demo V1.0 |

---

## 3. Kiến trúc hạ tầng dev

```text
                 ┌────────────────────── máy dev (host) ───────────────────────┐
                 │                                                             │
 Browser ──▶ Next.js :3000 ──REST /api/v1──▶ FastAPI :8000                     │
                 │                           │   │   │                         │
                 │          SQLAlchemy async │   │   │ DuckDB (embedded, in-process)
                 │                           ▼   │   ▼                         │
                 │   ┌──────── docker compose ─┼───────────────────────────┐   │
                 │   │  postgres :5432         │      minio :9000 (S3 API) │   │
                 │   │   ├─ db unilake         │            :9001 (console)│   │
                 │   │   │   ├─ schema app     └──▶  buckets:              │   │
                 │   │   │   └─ schema gold             bronze/ silver/ gold/│   │
                 │   │   └─ db unilake_source (giả lập hệ thống nguồn)     │   │
                 │   └────────────────────────────────────────────────────┘   │
                 └─────────────────────────────────────────────────────────────┘
```

| Service | Chạy ở đâu | Port | Ghi chú |
|---|---|---|---|
| PostgreSQL | Docker | 5432 | Nếu máy đã cài PostgreSQL local, đổi host port thành 5433 |
| MinIO API | Docker | 9000 | Endpoint S3 |
| MinIO Console | Docker | 9001 | Web UI xem bucket |
| DuckDB | **Không có container**, là thư viện Python chạy trong backend / script | — | Nhúng giống SQLite |
| Backend | Host (`uvicorn --reload`) khi dev; container khi tích hợp | 8000 | |
| Frontend | Host (`pnpm dev`) khi dev; container khi tích hợp | 3000 | |
| pgAdmin (tùy chọn) | Docker, profile `tools` | 5050 | |

**Nguyên tắc:** khi dev, chỉ hạ tầng chạy trong Docker; backend/frontend chạy trên host để hot reload. Code gọi `localhost`. Khi chạy trong container, compose override `DB_HOST=postgres`, `MINIO_ENDPOINT=minio:9000`.

---

## 4. Bước 0 – Công cụ trên máy

| Công cụ | Phiên bản | Kiểm tra |
|---|---|---|
| Docker Desktop (WSL2 backend trên Windows) | mới nhất | `docker version`, `docker compose version` |
| Python | 3.11 hoặc 3.12 | `py -3.11 --version` |
| Node.js | 20 hoặc 22 LTS | `node -v` |
| pnpm | 10 (`corepack enable`) | `pnpm -v` |
| Git | — | `git --version` |
| (tùy chọn) Tesseract OCR | 5.x | `tesseract --version` |
| (tùy chọn) DBeaver / pgAdmin | — | Xem DB |
| (tùy chọn) DuckDB CLI | cùng major với thư viện Python | `duckdb --version` |

Trên Windows, thêm `.gitattributes` để script shell trong container không bị lỗi CRLF:

```gitattributes
*.sh   text eol=lf
*.sql  text eol=lf
```

---

## 5. Bước 1 – Environment configuration (.env)

### 5.1 Quy ước

- `.env.example` được commit, chứa **giá trị dev an toàn**. `.env` không commit (đã có trong `.gitignore`).
- Tên biến giữ nguyên các biến `backend/app/core/config.py` đang đọc (`DB_*`, `MINIO_*`, `JWT_*`, `LLM_*`), chỉ **bổ sung**.
- Một file `.env` ở root dùng chung cho compose và backend. Frontend dùng `frontend/.env.local`.

### 5.2 `.env.example` đề xuất (đầy đủ)

```dotenv
# ===== Application =====
ENVIRONMENT=development
DEBUG=true
API_V1_PREFIX=/api/v1
CORS_ORIGINS=http://localhost:3000

# ===== PostgreSQL =====
DB_HOST=localhost
DB_PORT=5432
DB_USER=unilake
DB_PASSWORD=unilake
DB_NAME=unilake
DB_ECHO=false
# Role chỉ đọc cho NLQ/Dashboard (chỉ SELECT trên schema gold)
DB_READONLY_USER=unilake_reader
DB_READONLY_PASSWORD=unilake_reader
DB_STATEMENT_TIMEOUT_MS=5000
# Database giả lập hệ thống nguồn (Admissions/Training/HR)
DB_SOURCE_NAME=unilake_source

# ===== MinIO / S3 =====
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_SECURE=false
MINIO_REGION=us-east-1
MINIO_BUCKET_BRONZE=bronze
MINIO_BUCKET_SILVER=silver
MINIO_BUCKET_GOLD=gold

# ===== DuckDB =====
# :memory: cho backend (tránh khóa file); dùng file cho notebook/debug
DUCKDB_PATH=:memory:
DUCKDB_THREADS=4
DUCKDB_MEMORY_LIMIT=2GB

# ===== JWT =====
JWT_SECRET_KEY=change-me-in-production-use-openssl-rand-hex-32
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=1440

# ===== LLM =====
LLM_PROVIDER=mock          # mock | openai | anthropic
LLM_API_KEY=
LLM_MODEL=
LLM_TIMEOUT_SECONDS=30
```

`frontend/.env.local.example`:

```dotenv
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1
```

### 5.3 Cập nhật `config.py`

Thêm các field tương ứng vào `Settings` (cùng kiểu `Field(default=..., alias="...")` đang dùng), ví dụ `db_readonly_user`, `minio_bucket_bronze`, `duckdb_path`, và property `readonly_database_url`. Không đọc `os.environ` trực tiếp ở module khác, luôn đi qua `settings`.

---

## 6. Bước 2 – PostgreSQL

### 6.1 Thiết kế database

| Database / schema | Chứa gì | Ai ghi | Ai đọc |
|---|---|---|---|
| `unilake.app` | `app_user`, `role`, `ingestion_job`, `data_source`, `catalog_*`, `lineage_*`, `dq_result`, `query_request`… (phần app trong [unilake-db-architecture.txt](data-schema/unilake-db-architecture.txt)) | Backend (user `unilake`) qua Alembic | Backend |
| `unilake.gold` | Data mart đã tổng hợp (fact/dim theo 3 domain) | Pipeline (DuckDB `ATTACH` postgres) | NLQ, Dashboard bằng **`unilake_reader`** |
| `unilake_source` (database riêng) | Bảng nguồn Admissions/Training/HR, load từ `docs/data-schema/*_v1.sql` + `data/samples/*` | Seed script | Ingestion (như một nguồn SQL bên ngoài) |

Lý do tách `unilake_source`: ingestion phải đối xử với nó như hệ thống ngoài. Không để pipeline đọc thẳng bảng của `app`.

### 6.2 Init script

Tạo `infra/postgres/init/01-init.sh` (chỉ chạy **lần đầu** khi volume rỗng):

```sh
#!/bin/sh
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  CREATE SCHEMA IF NOT EXISTS app  AUTHORIZATION "$POSTGRES_USER";
  CREATE SCHEMA IF NOT EXISTS gold AUTHORIZATION "$POSTGRES_USER";

  -- Role chỉ đọc cho NLQ / Dashboard
  CREATE ROLE "$DB_READONLY_USER" LOGIN PASSWORD '$DB_READONLY_PASSWORD';
  GRANT CONNECT ON DATABASE "$POSTGRES_DB" TO "$DB_READONLY_USER";
  GRANT USAGE ON SCHEMA gold TO "$DB_READONLY_USER";
  GRANT SELECT ON ALL TABLES IN SCHEMA gold TO "$DB_READONLY_USER";
  ALTER DEFAULT PRIVILEGES FOR ROLE "$POSTGRES_USER" IN SCHEMA gold
    GRANT SELECT ON TABLES TO "$DB_READONLY_USER";
  ALTER ROLE "$DB_READONLY_USER" SET default_transaction_read_only = on;
  ALTER ROLE "$DB_READONLY_USER" SET statement_timeout = '${DB_STATEMENT_TIMEOUT_MS}ms';
  ALTER ROLE "$DB_READONLY_USER" SET search_path = gold;

  CREATE DATABASE "$DB_SOURCE_NAME" OWNER "$POSTGRES_USER";
EOSQL
```

Hai lớp bảo vệ cho SQL do LLM sinh: validator ở code **và** role chỉ đọc + timeout ở DB (baseline mục 8).

### 6.3 Alembic

- Model của backend đặt trong schema `app`: `Base.metadata = MetaData(schema="app")` hoặc `__table_args__ = {"schema": "app"}`.
- Trong `alembic/env.py`: `version_table_schema="app"`, `include_schemas=True`, và `include_object` bỏ qua schema `gold` (Gold do pipeline quản lý, không phải Alembic).
- Alembic dùng `settings.sync_database_url` hoặc async engine hiện có, không hard-code URL trong `alembic.ini`.

### 6.4 Kiểm tra

```bash
docker compose exec postgres psql -U unilake -d unilake -c "\dn"
docker compose exec postgres psql -U unilake_reader -d unilake -c "CREATE TABLE gold.x(id int);"
```

Lệnh thứ hai **phải lỗi** (`read-only transaction` / `permission denied`).

---

## 7. Bước 3 – MinIO (Object Storage)

### 7.1 Lưu ý về image

MinIO bản community đã thay đổi cách phát hành từ 2025 (bớt tính năng console, ngừng build image mới). Vì vậy:

- **Pin một tag `RELEASE.*` cụ thể** trong compose, không dùng `latest`. Kiểm tra tag còn tồn tại trên Docker Hub trước khi chốt. Tag trước `RELEASE.2025-05-24…` vẫn còn console quản trị đầy đủ.
- Quản lý bucket/user bằng `mc` (CLI), không phụ thuộc console.
- Nếu sau này image không kéo được, các S3-compatible thay thế (SeaweedFS, Garage) vẫn dùng được vì code chỉ nói chuyện qua S3 API.

### 7.2 Bucket

| Bucket | Nội dung | Định dạng |
|---|---|---|
| `bronze` | Dữ liệu thô, giữ nguyên bản + bản Parquet chuẩn hóa kiểu | File gốc (csv/xlsx/pdf/ảnh) + Parquet |
| `silver` | Dữ liệu đã làm sạch, chuẩn hóa | Delta table |
| `gold` | Data mart versioned (bản gốc để audit/time travel) | Delta table |

Tạo tự động bằng container `minio-init` (xem mục 10). Bật versioning cho `bronze` để không mất raw khi ghi đè nhầm.

### 7.3 Kiểm tra

- Console: <http://localhost:9001> (đăng nhập bằng `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`).
- CLI: `docker compose run --rm minio-init mc ls local` phải thấy 3 bucket.

---

## 8. Bước 4 – Cấu trúc Bronze / Silver / Gold

### 8.1 Quy ước đường dẫn

```text
bronze/{domain}/{source}/{entity}/ingest_date=YYYY-MM-DD/run_id={uuid}/
    ├── _raw/{original_file_name}          # file gốc, không sửa
    ├── data.parquet                       # đọc từ file gốc, thêm cột kỹ thuật
    └── _manifest.json                     # source, checksum, row_count, schema, ingested_at

silver/{domain}/{entity}/                  # 1 Delta table / entity, partition khi cần
    ├── _delta_log/
    └── part-*.parquet

gold/{mart}/                               # Delta table (versioned) – bản chính để query nằm ở PostgreSQL gold.{mart}
```

| Thành phần | Giá trị |
|---|---|
| `domain` | `admissions`, `training`, `hr` (V1.0, không `finance`) |
| `source` | tên data source đăng ký ở `datasources`, snake_case, ví dụ `csv_upload`, `source_db` |
| `entity` | tên bảng nguồn snake_case: `applicant`, `admission`, `student`, `employee`… |
| `mart` | `fact_admission`, `fact_enrollment`, `dim_major`, `dim_department`, `kpi_admission_by_major`… |

### 8.2 Quy tắc từng lớp

| Lớp | Được làm | Không được làm | Cột kỹ thuật bắt buộc |
|---|---|---|---|
| Bronze | Lưu nguyên bản, append-only, thêm metadata | Sửa, xóa, làm sạch dữ liệu | `_ingested_at`, `_source`, `_run_id`, `_file_name` |
| Silver | Ép kiểu, chuẩn hóa tên cột/giá trị, dedupe, tách bản ghi lỗi sang `silver/_quarantine/{domain}/{entity}/` | Tổng hợp nghiệp vụ | `_run_id`, `_processed_at`, `_is_valid` |
| Gold | Join, tổng hợp, KPI; mô hình star schema đơn giản | Chứa dữ liệu cá nhân không cần thiết cho phân tích | `_run_id`, `_published_at` |

### 8.3 Ranh giới với PostgreSQL

- Gold ghi **hai nơi**: Delta ở `gold/` (versioned) và bảng ở `unilake.gold` (phục vụ truy vấn nhanh, <5 giây). Quyết định D5, cần team xác nhận (mục 16).
- NLQ và Dashboard **chỉ** đọc `unilake.gold` bằng `unilake_reader`, không đọc Bronze/Silver.

---

## 9. Bước 5 – DuckDB + Delta Lake

### 9.1 Nguyên tắc

- DuckDB là **thư viện**, không phải server. Không cần container.
- Backend mở **connection in-memory mới cho mỗi job/request** (`DUCKDB_PATH=:memory:`). Dữ liệu bền vững nằm ở MinIO và PostgreSQL. Tránh file `.duckdb` dùng chung vì DuckDB chỉ cho **một process ghi** một file.
- Chỉ `infra/duckdb` được `import duckdb` (quy tắc R4).
- DuckDB **đọc** Delta tốt (`delta_scan`). Để **ghi** Delta, dùng thư viện `deltalake` (delta-rs), trao đổi dữ liệu qua Arrow.

### 9.2 Cấu hình kết nối (mẫu cho `infra/duckdb/connection.py`)

```python
import duckdb

from app.core.config import settings


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(settings.duckdb_path)
    con.execute(f"SET threads = {settings.duckdb_threads}")
    con.execute(f"SET memory_limit = '{settings.duckdb_memory_limit}'")
    for ext in ("httpfs", "delta", "postgres"):
        con.install_extension(ext)
        con.load_extension(ext)
    use_ssl = "true" if settings.minio_secure else "false"
    con.execute(
        f"""
        CREATE OR REPLACE SECRET minio (
            TYPE s3,
            KEY_ID '{settings.minio_access_key}',
            SECRET '{settings.minio_secret_key}',
            ENDPOINT '{settings.minio_endpoint}',
            REGION '{settings.minio_region}',
            URL_STYLE 'path',
            USE_SSL {use_ssl}
        )
        """
    )
    return con
```

> Câu `CREATE SECRET` không nhận tham số `?`, nên ghép chuỗi từ `settings`. Chỉ làm vậy với giá trị cấu hình do mình kiểm soát, không bao giờ với input người dùng.

Storage options cho `deltalake` (mẫu cho `infra/`):

```python
storage_options = {
    "AWS_ENDPOINT_URL": f"http://{settings.minio_endpoint}",
    "AWS_ACCESS_KEY_ID": settings.minio_access_key,
    "AWS_SECRET_ACCESS_KEY": settings.minio_secret_key,
    "AWS_REGION": settings.minio_region,
    "AWS_ALLOW_HTTP": "true",
    "AWS_S3_ALLOW_UNSAFE_RENAME": "true",  # dev, 1 writer; không dùng cho production
}
```

### 9.3 Luồng mẫu Bronze → Silver → Gold

```sql
-- Bronze: file gốc → Parquet
COPY (SELECT *, now() AS _ingested_at, 'csv_upload' AS _source
      FROM read_csv('data/samples/training/student.csv'))
TO 's3://bronze/training/csv_upload/student/ingest_date=2026-10-05/run_id=abc/data.parquet'
(FORMAT parquet);

-- Silver: đọc Bronze, làm sạch → Arrow → deltalake.write_deltalake('s3://silver/training/student', ...)
SELECT CAST(student_id AS BIGINT) AS student_id, trim(full_name) AS full_name, ...
FROM read_parquet('s3://bronze/training/csv_upload/student/**/data.parquet');

-- Gold: đọc Silver (Delta), ghi sang PostgreSQL
ATTACH 'host=localhost port=5432 dbname=unilake user=unilake password=unilake'
  AS pg (TYPE postgres);
CREATE OR REPLACE TABLE pg.gold.kpi_student_by_program AS
SELECT program_id, count(*) AS student_count
FROM delta_scan('s3://silver/training/student')
GROUP BY program_id;
```

---

## 10. Bước 6 – docker-compose.yml hoàn chỉnh

File mẫu thay cho `docker-compose.yml` đang rỗng. Mặc định `docker compose up -d` chỉ bật **hạ tầng**; backend/frontend nằm ở profile `app`.

```yaml
name: unilake

services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
      POSTGRES_DB: ${DB_NAME}
      DB_READONLY_USER: ${DB_READONLY_USER}
      DB_READONLY_PASSWORD: ${DB_READONLY_PASSWORD}
      DB_STATEMENT_TIMEOUT_MS: ${DB_STATEMENT_TIMEOUT_MS}
      DB_SOURCE_NAME: ${DB_SOURCE_NAME}
      TZ: Asia/Ho_Chi_Minh
    ports:
      - "${DB_PORT:-5432}:5432"
    volumes:
      - pg_data:/var/lib/postgresql/data
      - ./infra/postgres/init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER} -d ${DB_NAME}"]
      interval: 5s
      timeout: 5s
      retries: 10

  minio:
    image: minio/minio:RELEASE.2025-04-22T22-12-26Z   # pin tag, kiểm tra trên Docker Hub
    restart: unless-stopped
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ACCESS_KEY}
      MINIO_ROOT_PASSWORD: ${MINIO_SECRET_KEY}
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - minio_data:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s
      timeout: 5s
      retries: 10

  minio-init:
    image: minio/mc   # nên pin tag cùng thời điểm với minio
    depends_on:
      minio:
        condition: service_healthy
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio:9000 $${MINIO_ROOT_USER} $${MINIO_ROOT_PASSWORD} &&
      mc mb -p local/${MINIO_BUCKET_BRONZE} local/${MINIO_BUCKET_SILVER} local/${MINIO_BUCKET_GOLD} &&
      mc version enable local/${MINIO_BUCKET_BRONZE} &&
      mc ls local
      "
    environment:
      MINIO_ROOT_USER: ${MINIO_ACCESS_KEY}
      MINIO_ROOT_PASSWORD: ${MINIO_SECRET_KEY}
    restart: "no"

  backend:
    profiles: ["app"]
    build: ./backend
    env_file: .env
    environment:
      DB_HOST: postgres
      DB_PORT: 5432
      MINIO_ENDPOINT: minio:9000
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      minio-init:
        condition: service_completed_successfully

  frontend:
    profiles: ["app"]
    build: ./frontend
    environment:
      NEXT_PUBLIC_API_BASE_URL: http://localhost:8000/api/v1
    ports:
      - "3000:3000"
    depends_on:
      - backend

  pgadmin:
    profiles: ["tools"]
    image: dpage/pgadmin4
    environment:
      PGADMIN_DEFAULT_EMAIL: admin@unilake.local
      PGADMIN_DEFAULT_PASSWORD: admin
    ports:
      - "5050:80"
    depends_on:
      - postgres

volumes:
  pg_data:
  minio_data:
```

Lệnh thường dùng:

```bash
docker compose up -d                       # chỉ hạ tầng
docker compose ps                          # postgres, minio healthy; minio-init exited (0)
docker compose --profile app up -d --build # cả hệ thống (W10)
docker compose --profile tools up -d pgadmin
docker compose down                        # dừng, giữ dữ liệu
docker compose down -v                     # xóa sạch dữ liệu, init script chạy lại
```

Nên thêm vào `Makefile`: `infra-up`, `infra-down`, `infra-reset`, `smoke-test`, `seed-source`.

---

## 11. Bước 7 – Kiểm tra đọc/ghi và giao tiếp service (smoke test)

Tạo `infra/scripts/smoke_test.py`, chạy từ host sau `docker compose up -d`. Mỗi bước in `OK` / `FAIL`.

| # | Kiểm tra | Cách | Kỳ vọng |
|---|---|---|---|
| 1 | Backend ↔ PostgreSQL | `asyncpg`/SQLAlchemy `SELECT 1` bằng `DB_USER` | OK |
| 2 | Schema tồn tại | `SELECT schema_name FROM information_schema.schemata` | Có `app`, `gold` |
| 3 | Role chỉ đọc | Kết nối `unilake_reader`, thử `CREATE TABLE gold.t(id int)` | **Bị từ chối** |
| 4 | Backend ↔ MinIO | `minio` SDK: `bucket_exists` cho 3 bucket; `put_object` + `get_object` file test ở `bronze/_smoke/` | OK, nội dung khớp |
| 5 | DuckDB → MinIO (ghi) | `COPY (SELECT 1 AS id) TO 's3://bronze/_smoke/t.parquet'` | OK |
| 6 | DuckDB ← MinIO (đọc) | `SELECT count(*) FROM read_parquet('s3://bronze/_smoke/t.parquet')` | 1 |
| 7 | Delta ghi/đọc | `deltalake.write_deltalake('s3://silver/_smoke/t', ...)` rồi `delta_scan` | 1 dòng, version 0 |
| 8 | DuckDB → PostgreSQL | `ATTACH ... (TYPE postgres)`; `CREATE OR REPLACE TABLE pg.gold._smoke AS SELECT 1 AS id` | OK |
| 9 | Reader đọc Gold | `unilake_reader`: `SELECT * FROM gold._smoke` | 1 dòng |
| 10 | Dọn dẹp | Xóa `_smoke` ở MinIO và `gold._smoke` | OK |
| 11 | Container ↔ container | `docker compose --profile app up -d`, gọi `GET http://localhost:8000/health/ready` | `postgres: ok, minio: ok` |

Cho bước 11, backend nên có endpoint `/health/ready` kiểm tra PostgreSQL + MinIO (khác `/health` chỉ báo process sống). Endpoint này cũng dùng làm healthcheck của container backend.

---

## 12. Bước 8 – Backend (FastAPI)

### 12.1 Dependency cần bổ sung / chỉnh

```toml
dependencies = [
  # đã có: fastapi, uvicorn[standard], pydantic(-settings), sqlalchemy, asyncpg, alembic,
  #        greenlet, duckdb, minio, langchain, great-expectations
  "deltalake>=1.0",
  "pyarrow>=17",
  "sqlglot>=25",
  "pyjwt>=2.9",
  "pwdlib[argon2]>=0.2",
  "python-multipart>=0.0.9",   # upload file / OAuth2 form
  "apscheduler>=3.10",         # job nền (D6), nếu cần lập lịch
  "psycopg[binary]>=3.2",      # Alembic sync + DuckDB/GE nếu cần
]
# gỡ: "ortools" (ngoài phạm vi V1.0)

[project.optional-dependencies]
llm = ["langchain-openai", "langchain-anthropic"]
ingest-docs = ["pdfplumber", "python-docx", "openpyxl"]
ocr = ["pillow", "pytesseract"]
dev = [ ... , "testcontainers[postgres,minio]"]  # integration test (tùy chọn)
```

Cài: `pip install -e "backend[dev,llm,ingest-docs]"`.

### 12.2 Chuẩn đã có trong repo, giữ nguyên

- Modular monolith, module trong `app/modules/*`, ranh giới kiểm bởi `tests/test_module_boundaries.py`.
- Adapter ngoài trong `app/infra/{db,minio,duckdb,llm}`; module không import SDK trực tiếp.
- Ruff + Mypy strict + pytest coverage ≥70% + pre-commit + Conventional Commits.

### 12.3 Chuẩn cần bổ sung

| Hạng mục | Chuẩn |
|---|---|
| API | Prefix `/api/v1`, REST JSON, lỗi chuẩn `{code, message}` + header `X-Request-ID` |
| Auth | OAuth2 password flow → JWT access token; RBAC theo role trong `app.role`; hash mật khẩu Argon2 |
| CORS | Chỉ cho `CORS_ORIGINS` |
| DB session | 1 engine ghi (`unilake`) + 1 engine đọc Gold (`unilake_reader`) trong `infra/db` |
| Job dài | `POST` trả `job_id`, trạng thái trong `app.ingestion_job`, Frontend poll |
| Logging | Log JSON có `request_id`, không log mật khẩu/API key |
| OpenAPI | Export `openapi.json` vào `docs/api/` mỗi khi API đổi |

Chạy dev: `uvicorn app.main:app --app-dir backend --reload --port 8000` (như `SETUP.md`).

---

## 13. Bước 9 – Frontend (Next.js)

### 13.1 Stack

| Hạng mục | Công nghệ | Trạng thái |
|---|---|---|
| Framework | Next.js 15 App Router, React 19, TypeScript strict | Có |
| Styling | TailwindCSS 3.4 | Có |
| Component | shadcn/ui (Radix) + `lucide-react` icon | Thêm |
| Chart | Recharts (proposal) | Thêm |
| Server state | TanStack Query | Thêm |
| API client | `openapi-typescript` + `openapi-fetch` (sinh type từ `docs/api/openapi.json`) | Thêm |
| Form / validate | `react-hook-form` + `zod` | Thêm |
| Test | Vitest + Testing Library (unit), Playwright (e2e) | Vitest có; Playwright có script nhưng chưa có dependency |
| Quality | ESLint 9, Prettier, Lighthouse CI | Có |

Cài thêm:

```bash
cd frontend
pnpm add recharts @tanstack/react-query openapi-fetch zod react-hook-form @hookform/resolvers lucide-react
pnpm add -D openapi-typescript @testing-library/react @testing-library/jest-dom jsdom @playwright/test
pnpm dlx shadcn@latest init
```

Script sinh type (thêm vào `package.json`):

```json
"gen:api": "openapi-typescript ../docs/api/openapi.json -o src/lib/api/schema.d.ts"
```

### 13.2 Cấu trúc và quy ước

```text
src/
├── app/
│   ├── (auth)/login/
│   ├── (dashboard)/overview/        # KPI, chart
│   ├── (dashboard)/ask/             # Chatbot NLQ
│   ├── (dashboard)/governance/      # Catalog, Lineage, DQ report
│   ├── (dashboard)/ingestion/       # Nguồn dữ liệu, job
│   └── layout.tsx
├── components/{ui,charts,layout}/
├── lib/api/                         # client.ts + schema.d.ts (sinh tự động)
├── hooks/  types/  constants/  styles/
└── middleware.ts                    # chặn route khi chưa đăng nhập
```

- Frontend **chỉ** gọi REST của backend, không truy cập DB/MinIO/LLM (R7).
- Token JWT: lưu trong cookie `httpOnly` (qua Next.js route handler), không lưu `localStorage`.
- Không hard-code URL API, dùng `NEXT_PUBLIC_API_BASE_URL`.

---

## 14. Bước 10 – CI/CD và chất lượng

| Việc | Khi nào |
|---|---|
| Giữ workflow hiện có (`ci-backend`, `ci-frontend`, `code-quality`) | Có sẵn |
| Thêm `docker compose config` vào CI để bắt lỗi YAML/biến môi trường | W2 |
| Job integration test dùng GitHub Actions `services:` (postgres) + MinIO, chạy smoke test | W3–W5 |
| Build image backend/frontend trong CI (không push) | W10 |
| Kiểm tra `docs/api/openapi.json` khớp với app (fail nếu quên export) | W8 |

---

## 15. Definition of Done cho task hạ tầng W2

Task "Thiết lập hạ tầng dữ liệu cơ bản" được coi là xong khi:

- [ ] `docker compose up -d` trên máy sạch (Windows + macOS/Linux) chạy được, `postgres` và `minio` ở trạng thái `healthy`, `minio-init` exit 0.
- [ ] PostgreSQL có database `unilake` (schema `app`, `gold`), database `unilake_source`, role `unilake_reader` chỉ đọc có `statement_timeout`.
- [ ] MinIO có bucket `bronze`, `silver`, `gold`; `bronze` bật versioning.
- [ ] Quy ước đường dẫn và cột kỹ thuật Bronze/Silver/Gold (mục 8) được team duyệt.
- [ ] `.env.example` và `frontend/.env.local.example` đầy đủ biến; `config.py` đọc được các biến mới.
- [ ] `infra/duckdb` tạo được connection có `httpfs`, `delta`, `postgres` và secret S3.
- [ ] Smoke test (mục 11) bước 1–10 pass.
- [ ] `SETUP.md` cập nhật: bỏ Redis, thêm lệnh smoke test và bảng port.
- [ ] Không có secret thật nào bị commit.

Đạt các mục trên thì W3 Data Ingestion có thể bắt đầu ngay: có nơi lưu raw (MinIO Bronze), có database cho structured data (PostgreSQL), có engine xử lý/query (DuckDB).

---

## 16. Việc team cần chốt

| # | Câu hỏi | Đề xuất |
|---|---|---|
| 1 | Gold phục vụ truy vấn ở PostgreSQL `gold` hay DuckDB đọc Delta trực tiếp? (D5) | PostgreSQL `gold` cho query; Delta ở MinIO giữ bản versioned |
| 2 | Job nền: in-process hay Celery/Redis? (D6) | In-process + trạng thái ở PostgreSQL |
| 3 | OpenMetadata có dựng không? | Không trong compose mặc định; catalog/lineage ở PostgreSQL `app` |
| 4 | Gỡ `ortools` khỏi `pyproject.toml`? | Gỡ (ngoài phạm vi V1.0) |
| 5 | LLM provider mặc định khi dev? | `mock` mặc định; mỗi người tự điền key vào `.env` riêng |
| 6 | Tag MinIO pin cụ thể | Chốt một tag, cả team dùng chung |
| 7 | Có dùng database `unilake_source` giả lập nguồn không, hay chỉ ingest CSV? | Có, để demo được cả nguồn file và nguồn SQL |

---

## 17. Troubleshooting (Windows)

| Sự cố | Nguyên nhân | Cách xử lý |
|---|---|---|
| `port is already allocated` 5432 | Máy đã cài PostgreSQL local | Đổi `DB_PORT=5433` trong `.env` (compose map `5433:5432`) |
| Init script không chạy / `\r: not found` | File `.sh` bị CRLF, hoặc volume đã tồn tại | Thêm `.gitattributes` (mục 4), `docker compose down -v` rồi up lại |
| Sửa init script nhưng DB không đổi | Init chỉ chạy khi volume rỗng | `docker compose down -v` (mất dữ liệu dev) |
| DuckDB `HTTP 403` / `SignatureDoesNotMatch` với MinIO | Sai `URL_STYLE`, region, hoặc endpoint có `http://` | `URL_STYLE 'path'`, `ENDPOINT 'localhost:9000'` (không kèm scheme), `USE_SSL false` |
| `deltalake` lỗi khi ghi lên MinIO | Thiếu `AWS_ALLOW_HTTP` / `AWS_S3_ALLOW_UNSAFE_RENAME` | Dùng đúng `storage_options` mục 9.2 |
| DuckDB `Could not set lock on file` | Hai process cùng mở một file `.duckdb` | Dùng `:memory:` cho backend; file chỉ cho notebook |
| DuckDB không tải được extension | Máy offline / chặn mạng | Cài extension một lần khi có mạng; chúng được cache ở `~/.duckdb/extensions` |
| Backend trong container không kết nối DB | Dùng `localhost` bên trong container | Compose override `DB_HOST=postgres`, `MINIO_ENDPOINT=minio:9000` |
| `python` mở Microsoft Store | Alias Windows | Dùng `py -3.11` hoặc tắt App execution aliases |
