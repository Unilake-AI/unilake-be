# System Architecture Baseline – UniLake AI V1.0

> Trạng thái: **Baseline đề xuất, chờ team review và chốt.** Nguồn chuẩn về phạm vi là proposal `C1SE_11_ProjectProposal` (mục 6, 10, 12). Khi tài liệu này và README mâu thuẫn, proposal thắng.
>
> Sơ đồ tương tác: [unilake-system-architecture-baseline.html](unilake-system-architecture-baseline.html) (nguồn: `.architecture.json` cùng thư mục). Sơ đồ liên quan: [System Context](unilake-system-context-v1.html), [NLQ Flow](unilake-nlq-flow.html), [Use Case](unilake-use-case.html).

## 1. Mục đích và phạm vi

Tài liệu này thống nhất kiến trúc trước khi các thành viên phát triển module độc lập. Nó trả lời chín câu hỏi: các layer, ranh giới module, luồng dữ liệu, cách Frontend gọi Backend, cách AI truy cập schema và dữ liệu, cách Governance nối với Data Lake, cách tích hợp LLM, phụ thuộc giữa các module, và sơ đồ baseline.

**Trong phạm vi V1.0:** ba domain Admissions, Academic/Training, Human Resources; Data Lake Bronze → Silver → Gold; Governance (Catalog, Metadata, Lineage, Quality); AI Analytics gồm Natural Language Query (NLQ), Text-to-SQL, SQL Validation, Query Execution; Web Dashboard.

**Ngoài phạm vi V1.0 (proposal):** domain Finance, Constraint Optimization / OR-Tools, What-If nâng cao, fine-tune LLM, real-time streaming, tích hợp production.

## 2. Nguyên tắc kiến trúc

1. **Modular Monolith:** một process FastAPI, một lần deploy, nhiều module có trách nhiệm rõ ràng.
2. **Phụ thuộc một chiều, không vòng:** module cấp cao gọi module cấp thấp qua giao diện công khai.
3. **Mọi thứ bên ngoài đi qua `infra/`:** database, MinIO, DuckDB, LLM. Module nghiệp vụ không import SDK trực tiếp.
4. **LLM không bao giờ là nguồn tin cậy:** SQL do LLM sinh luôn phải qua validation và chạy bằng quyền chỉ đọc.
5. **Hợp đồng trước, code sau:** giao tiếp giữa các thành viên dựa trên OpenAPI (Frontend ↔ Backend) và service interface (module ↔ module).

## 3. Các layer

| # | Layer (proposal 12.2) | Thành phần | Vị trí trong repo |
|---|---|---|---|
| 1 | Presentation | Next.js: Dashboard, KPI, Charts, Chatbot NLQ, Governance View | `frontend/` |
| 2 | Backend / Application | FastAPI: Authentication, User, Dataset, Query, Governance, AI Analytics, Dashboard API | `backend/app/main.py`, `core/`, `modules/*/router.py` |
| 3 | AI Analytics | Intent Parser, Schema Retrieval, Text-to-SQL, SQL Validation, Executor | `modules/nlq/` |
| 4 | Data Governance | Catalog, Metadata, Lineage, Quality | `modules/governance/` |
| 5 | Data | Bronze (MinIO), Silver (DuckDB + Delta), Gold (PostgreSQL + Delta), schema app (PostgreSQL) | `infra/` + hạ tầng Docker |

Layer 3 và 4 **là module trong cùng process** với layer 2. Chúng tách khỏi Application để có ranh giới trách nhiệm, không phải service riêng.

Thư mục dùng chung:

| Thư mục | Vai trò | Được import bởi | Được import từ |
|---|---|---|---|
| `core/` | Config, security (JWT), dependency chung, exception, logging | mọi nơi | `infra/`, `shared/` |
| `infra/` | Adapter ra hệ thống ngoài: `db`, `minio`, `duckdb`, `llm` | `modules/`, `core/deps` | `core/config` |
| `shared/` | Tiện ích và schema dùng chung, không có nghiệp vụ | mọi nơi | không import module nào |

## 4. Ranh giới trách nhiệm module

| Module | Chức năng proposal | Trách nhiệm | Không được làm |
|---|---|---|---|
| `auth` + `users` | Authentication, User | Đăng nhập, cấp và xác thực JWT, RBAC, hồ sơ người dùng | Chứa logic nghiệp vụ dữ liệu |
| `datasources` | Dataset | Đăng ký nguồn dữ liệu, cấu hình kết nối, danh sách dataset | Chạy pipeline, đọc dữ liệu lake |
| `ingestion` | Ingestion Pipeline | Trigger và theo dõi job; ghi Bronze; chạy làm sạch Silver và tổng hợp Gold; phát sự kiện lineage và gọi DQ | Sinh SQL, tự lưu catalog |
| `governance` | Governance | Catalog, Metadata, Lineage, Data Quality (Great Expectations). Cung cấp schema context cho AI | Chạy ETL, gọi LLM |
| `nlq` | AI Analytics, Query | Intent → Schema Retrieval → Text-to-SQL → Validation → Execution. Gọi LLM qua `infra/llm` | Đọc Bronze/Silver, ghi dữ liệu, lưu catalog |
| `dashboard` | Dashboard API | KPI, tổng hợp và dữ liệu cho biểu đồ, đọc Gold chỉ đọc | Sinh SQL từ ngôn ngữ tự nhiên |
| `query_history` | Query | Lưu và trả lịch sử câu hỏi, SQL, kết quả tóm tắt | Thực thi truy vấn |

Sở hữu module (đề xuất theo vai trò trong README, cần team xác nhận):

| Module | Owner đề xuất |
|---|---|
| App shell, `core/`, `auth`, `users`, kiến trúc chung | Nguyễn Thanh Sơn |
| `datasources`, `ingestion`, Bronze/Silver/Gold, `dashboard` (phần dữ liệu) | Hoàng Lâm Bảo Toàn |
| `governance` | Hoàng Lâm Bảo Toàn, phối hợp Sơn |
| `nlq`, `infra/llm`, prompts | Đặng Trần Trí Đức |
| `frontend/` | Nguyễn Thị Tố Loan |
| `docker-compose`, CI/CD, test tích hợp | Trương Đình Đạt |

## 5. Phụ thuộc giữa các module

### 5.1 Ma trận cho phép

Hàng phụ thuộc cột. Mọi ô trống là **cấm**. Ma trận này được kiểm tự động bởi [`backend/tests/test_module_boundaries.py`](../../backend/tests/test_module_boundaries.py).

| Module ↓ dùng → | auth | users | datasources | ingestion | governance | nlq | dashboard | query_history |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| `auth` | | ✔ | | | | | | |
| `users` | | | | | | | | |
| `datasources` | | | | | | | | |
| `ingestion` | | | ✔ | | ✔ | | | |
| `governance` | | | | | | | | |
| `nlq` | | | | | ✔ | | | ✔ |
| `dashboard` | | | | | | | | |
| `query_history` | | | | | | | | |

Đồ thị phụ thuộc: `ingestion → datasources`, `ingestion → governance`, `nlq → governance`, `nlq → query_history`, `auth → users`. Không có vòng.

### 5.2 Quy tắc

- **R1.** Module chỉ gọi module khác qua `service` hoặc `schemas` công khai. Không import `models`, `repository`, `router` của module khác.
- **R2.** Chỉ được phụ thuộc theo ma trận 5.1. Cần cạnh mới thì sửa ma trận, test và tài liệu này trong cùng một PR.
- **R3.** `core/`, `infra/`, `shared/` không import `modules/`.
- **R4.** SDK ngoài (`openai`, `anthropic`, `minio`, `duckdb`, `langchain`, `boto3`) chỉ được import trong `infra/`.
- **R5.** Module cũ `ingest`, `pipeline`, `query`, `ai_engine` chỉ được re-export module mới; không viết code mới ở đó.
- **R6.** Bảng cần migration đặt model trong `models.py` của đúng module, kế thừa `Base` chung.
- **R7.** Frontend không truy cập DB, MinIO hay LLM; chỉ gọi REST của Backend.

## 6. Luồng dữ liệu tổng thể

```text
Nguồn (Admissions / Academic / HR / CSV, Excel, API)
   │  batch · file · API
   ▼
ingestion ──ghi raw──▶ Bronze (MinIO/S3, Delta)
   │ làm sạch, chuẩn hóa (DuckDB)
   ▼
Silver (Delta)
   │ tổng hợp thành data mart
   ▼
Gold (PostgreSQL + Delta) ──▶ nlq, dashboard (chỉ đọc)

governance: ghi Catalog, Lineage, kết quả DQ cho cả 3 lớp
           ──▶ nlq lấy schema context từ Catalog
```

Luồng ingest (ghi):

1. `datasources` giữ cấu hình nguồn. `ingestion` đọc cấu hình đó.
2. `ingestion` ghi dữ liệu thô vào Bronze và tạo job (trạng thái lưu ở schema app).
3. Sau mỗi bước Bronze → Silver → Gold, `ingestion` gọi `governance` để ghi lineage, cập nhật metadata và chạy DQ check.
4. Chỉ dataset đạt ngưỡng DQ mới được đánh dấu `published` trong Catalog. `nlq` chỉ nhìn thấy dataset `published` của Gold.

Luồng truy vấn (đọc): xem mục 8.

## 7. Frontend giao tiếp với Backend

| Hạng mục | Quyết định |
|---|---|
| Giao thức | REST JSON qua HTTPS, prefix `/api/v1`, một app shell FastAPI duy nhất |
| Xác thực | JWT Bearer trong header `Authorization`; RBAC theo role (`require_role`) |
| Hợp đồng | OpenAPI tự sinh từ FastAPI (`/docs`). Frontend sinh TypeScript type từ OpenAPI; bản đặc tả xuất ra `docs/api/` mỗi khi API đổi |
| Lỗi | Một dạng lỗi chuẩn từ `AppException` (`code`, `message`), kèm header `X-Request-ID` |
| Tác vụ ngắn | Đồng bộ. NLQ mục tiêu dưới 5 giây |
| Tác vụ dài (ingestion) | `POST` trả `job id`, Frontend poll `GET /ingestion/jobs/{id}`. V1.0 không dùng WebSocket/streaming |
| Trách nhiệm Frontend | Hiển thị Dashboard, Chatbot, Governance View; không giữ logic SQL hay quyền dữ liệu |

Nhóm endpoint theo module: `/auth`, `/users`, `/datasources`, `/ingestion`, `/governance`, `/nlq`, `/dashboard`, `/query-history` (đã có router rỗng tương ứng trong `main.py`).

## 8. AI Analytics truy cập schema và dữ liệu

`nlq` dùng **hai đường tách biệt**: đường metadata (lấy schema) và đường dữ liệu (chạy SQL).

```text
câu hỏi ─▶ parse_intent ─▶ retrieve_schema ─▶ generate_sql ─▶ validate_sql ─▶ execute ─▶ kết quả
                              │                    │                              │
                        governance.Catalog      infra/llm ─▶ LLM API        Gold, chỉ đọc
                        (schema context)      (chỉ nhận câu hỏi + schema)
```

- **Schema:** `schema_retriever` gọi service của `governance` để lấy bảng, cột, mô tả và ví dụ giá trị của các dataset `published` trong Gold, lọc theo intent. Không đọc `information_schema` trực tiếp. Nhờ vậy Catalog là nguồn duy nhất và LLM không thấy bảng chưa được duyệt.
- **Sinh SQL:** `sql_generator` gửi prompt (câu hỏi + schema context) tới `LLMClient` trong `infra/llm`. Prompt mẫu được version trong `nlq/prompts/`. Không gửi dữ liệu dòng vào LLM, chỉ schema.
- **Validation:** `sql_validator` chấp nhận đúng một câu `SELECT`, chỉ tham chiếu bảng nằm trong schema context, không có DDL/DML, có `LIMIT` mặc định.
- **Thực thi:** `executor` chạy qua một cổng đọc Gold duy nhất trong `infra/`, dùng kết nối chỉ đọc (role PostgreSQL chỉ có `SELECT` trên schema `gold`), có timeout. Đây là lớp bảo vệ thứ hai nếu validation sót.
- **Ghi nhận:** kết quả và SQL được lưu qua `query_history`.
- **Lỗi LLM:** timeout hoặc lỗi thì trả lỗi rõ ràng, có thể thử lại một lần. Không tự thay bằng SQL giả.

## 9. Data Governance liên kết với Data Lake

- `governance` **không chạy ETL**. Nó nhận sự kiện từ `ingestion` (dataset mới, bước biến đổi vừa xong) và tự đọc metadata của lớp tương ứng qua `infra/`.
- **Catalog và Metadata:** mỗi dataset ở Bronze, Silver, Gold có một bản ghi (tên, domain, schema, owner, lớp, phiên bản Delta, trạng thái `draft` / `published`).
- **Lineage:** cạnh `nguồn → Bronze → Silver → Gold` được ghi ở mức dataset và cột chính. Data Administrator xem qua Governance View.
- **Quality:** bộ rule (Great Expectations) chạy theo dataset và theo lớp. Kết quả đi vào Quality Report; ngưỡng đạt mục tiêu là DQ pass rate từ 90%.
- **Lưu trữ metadata:** V1.0 lưu Catalog, Lineage, kết quả DQ trong schema `app` của PostgreSQL. OpenMetadata, nếu dùng, chỉ là adapter đồng bộ thêm trong `infra/`, không phải phụ thuộc bắt buộc của `nlq`.

## 10. Dịch vụ ngoài

| Dịch vụ | Cách tích hợp | Quy tắc |
|---|---|---|
| LLM API (OpenAI / Anthropic) | Interface `LLMClient` trong `infra/llm`; chọn provider bằng `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL` | Chỉ `nlq` dùng; có `MockLLMClient` cho test và dev; timeout, retry một lần, giới hạn số call theo cấu hình |
| Object Storage (MinIO / S3) | `infra/minio` | Chỉ lưu Bronze và Delta; module nghiệp vụ không tự tạo client |
| PostgreSQL | `infra/db` (SQLAlchemy async, Alembic) | Schema `app` cho state, schema `gold` cho data mart |
| DuckDB | `infra/duckdb` | Engine xử lý ETL ở Silver và Gold |

Khóa API và mật khẩu chỉ đến từ biến môi trường (`.env`), không commit.

## 11. Các quyết định kiến trúc

| ID | Quyết định | Trạng thái |
|---|---|---|
| D1 | Modular Monolith một process FastAPI | Đã chốt (proposal 12) |
| D2 | Ba domain, không Finance, không OR-Tools/What-If | Đã chốt (proposal) |
| D3 | Một app shell, REST `/api/v1`, JWT, OpenAPI là hợp đồng | Đề xuất |
| D4 | `nlq` lấy schema từ `governance` (Catalog), không đọc trực tiếp DB | Đề xuất |
| D5 | Gold phục vụ truy vấn nằm ở schema `gold` của PostgreSQL, đọc bằng role chỉ đọc; Delta giữ bản versioned | **Cần team xác nhận** (xem 12, mục 3) |
| D6 | Job nền V1.0 chạy trong process (BackgroundTasks / APScheduler), trạng thái ở PostgreSQL. Chưa cần Celery hay Redis | **Cần team xác nhận** |
| D7 | Catalog, Lineage, DQ lưu ở PostgreSQL; OpenMetadata là tùy chọn | Đề xuất |
| D8 | Kiểm tra ranh giới module bằng test tự động trong CI | Đã thực hiện |

## 12. Lệch giữa baseline và hiện trạng repo

Đây là các việc cần xử lý, không chặn việc bắt đầu code module.

1. **README lệch proposal:** mục tiêu 5 còn nhắc AI Decision Engine và 10 kịch bản What-If; bảng công nghệ còn Celery/ARQ, VectorDB, `ortools` (có trong `backend/pyproject.toml`). Cần team quyết định giữ hay bỏ.
2. **`docs/backend.md` còn liệt kê module cũ** (`ingest`, `pipeline`, `query`, `ai_engine`) ở bảng chính. Nên chuyển sang bảng ở mục 4.
3. **Gold nằm ở đâu:** `infra/duckdb` mô tả "Gold layer analytical queries" trong khi README ghi Gold ở PostgreSQL. D5 chọn PostgreSQL cho truy vấn; nếu team muốn DuckDB đọc Delta thì đổi cổng đọc trong `infra/`, không đổi module.
4. **`docker-compose.yml` đang rỗng** dù README hướng dẫn `docker compose up`. Cần PostgreSQL và MinIO.
5. **`nlq/router.py` đang mock:** dùng `MockLLMClient` và thay SQL không bắt đầu bằng `select` bằng `select 1`, tức là bỏ qua validator thay vì báo lỗi. Cần bỏ trước khi nối LLM thật.
6. **`sql_validator` còn thô:** lọc theo chuỗi con (ví dụ `update ` chặn cả tên cột hợp lệ) và chưa kiểm tra bảng có nằm trong schema context. Cần parser SQL thật (ví dụ `sqlglot`).
7. **Trùng `Base`/session:** `core/database.py` chỉ re-export `infra/db/session.py`. Nên chuẩn hóa import về `app.infra.db` khi có thời gian.
8. **Chưa có `service.py`** ở các module. Theo R1, thành viên tạo `service.py` làm giao diện công khai khi bắt đầu module của mình.
9. **Alias module cũ:** xóa `ingest`, `pipeline`, `query`, `ai_engine` sau khi không còn nơi nào dùng (test đã khóa chúng chỉ re-export).

## 13. Checklist khi thêm hoặc sửa module

1. Xác định module thuộc layer nào và có nằm trong ma trận mục 5.1 chưa.
2. Cần gọi module khác? Kiểm tra cạnh đã được phép; nếu chưa, sửa ma trận, `ALLOWED_DEPENDENCIES` trong test và tài liệu này trong cùng PR.
3. Chỉ expose `service.py` và `schemas.py`; giữ `models.py`, `repository.py` nội bộ.
4. Truy cập DB, MinIO, DuckDB, LLM chỉ qua `infra/`.
5. Đổi API thì cập nhật OpenAPI trong `docs/api/` để Frontend sinh lại type.
6. Chạy `pytest` trong `backend/`; `tests/test_module_boundaries.py` phải xanh.
