# Module Boundary - Questions and Architecture Decisions

## Mục đích

Tài liệu này trả lời các câu hỏi trong task Module Boundary và chốt quyết định áp dụng cho codebase
UniLake AI. Task này kế thừa nền tảng từ Domain Extensibility nhưng có mục tiêu khác:

```text
Domain Extensibility
-> Có thể thêm domain mới mà không sửa core hay không?

Module Boundary
-> Có thể thay đổi implementation của một module mà không kéo theo thay đổi dây chuyền hay không?
```

Task không yêu cầu thêm feature hoặc production domain mới. Trọng tâm là responsibility ownership,
communication contract, dependency direction và automated architecture checks.

## Câu hỏi 1: Trách nhiệm của từng module là gì?

### Quyết định

| Module/package | Trách nhiệm chính | Không được sở hữu |
| :--- | :--- | :--- |
| `core` | Config, security primitives, exception, logging và cross-cutting foundation | Business/domain logic, ETL, catalog hoặc query workflow |
| `infra` | PostgreSQL, MinIO, DuckDB, LLM và các technology adapters | Business rule, domain schema hoặc use-case orchestration |
| `domains` | Schema, mapping, transformation, quality rule và semantic metadata theo domain | Router, job scheduling, database/storage client |
| `auth` | Authentication, credential/token lifecycle và authorization boundary | User profile hoặc pipeline access |
| `users` | User profile, account state và role assignment | Token issuance hoặc credential verification |
| `datasources` | Source metadata và connection-configuration reference | Đọc source thành batch hoặc transform Silver/Gold |
| `ingestion` | Source to Bronze, ingestion job và Bronze batch hand-off | Cleaning, normalization hoặc Gold analytics |
| `pipeline` | Bronze to Silver to Gold orchestration, quality gates và publication | Concrete domain rule hoặc vendor storage implementation |
| `governance` | Catalog, lineage, static quality metadata và runtime quality evidence | Tự chạy ingestion/pipeline hoặc định nghĩa domain rule |
| `nlq` | Intent, schema retrieval, SQL generation, validation và read-only execution | Hard-code domain, write SQL hoặc dashboard presentation |
| `dashboard` | KPI/read models và API dữ liệu tổng hợp cho UI | ETL, NLQ hoặc định nghĩa Gold metric gốc |
| `query_history` | Lịch sử câu hỏi, SQL, execution status và audit metadata | Sinh hoặc thực thi SQL |
| `shared` | Primitive/utility nhỏ, ổn định và domain-neutral dùng bởi nhiều module | Business service hoặc code chưa xác định ownership |

Chi tiết đầy đủ được chốt trong
[`backend-module-responsibilities.md`](backend-module-responsibilities.md).

## Câu hỏi 2: Module giao tiếp với nhau bằng cách nào?

### Quyết định

Có bốn hình thức giao tiếp được phép:

1. **Synchronous service call:** caller cần kết quả ngay và gọi public service/facade.
2. **Command:** caller yêu cầu một owner thực hiện hành động cụ thể.
3. **Typed event:** producer thông báo fact đã xảy ra, không biết consumer cụ thể.
4. **Shared read contract:** dashboard/NLQ đọc published Gold hoặc catalog qua read port.

Quy tắc bắt buộc:

- Module nội bộ không gọi HTTP endpoint của nhau.
- Không import router, repository hoặc ORM model của module khác.
- Qua boundary chỉ truyền ID, typed DTO, command hoặc immutable event.
- Không truyền SQLAlchemy session, ORM entity, FastAPI object hoặc vendor client.
- Workflow dài như Bronze to Pipeline dùng event contract; V1 có thể dispatch trong process.
- Infrastructure được gọi qua port/Protocol và được inject tại composition root.

Chi tiết được chốt trong
[`backend-module-communication.md`](backend-module-communication.md).

## Câu hỏi 3: Dependency direction được xác định thế nào?

### Quyết định

```text
main.py / delivery adapters
          |
          v
application modules and services
          |
          v
domain contracts + core/shared primitives

infrastructure adapters
          |
          +-> implement application-owned ports
```

- Router phụ thuộc service; service không phụ thuộc router.
- Service phụ thuộc port; infrastructure adapter implement port.
- Generic modules phụ thuộc `domains/base` và registry, không phụ thuộc concrete domain.
- Concrete domain chỉ phụ thuộc domain base và domain-neutral utility.
- Cross-module import chỉ được phép qua public contract/service.
- `main.py` là nơi wiring abstraction với implementation.
- Không cho phép circular import hoặc circular service calls.

`infra/minio` được phép import `pipeline/contracts` để implement storage port, nhưng không được import
`pipeline/service`. Đây là Dependency Inversion, không phải dependency ngược sai kiến trúc.

Ma trận import và các exception hiện tại được ghi tại
[`backend-dependency-direction.md`](backend-dependency-direction.md).

## Câu hỏi 4: `ai_engine` làm gì?

### Trả lời

`ai_engine` không phải canonical business module và đã được xóa sau khi xác nhận toàn repo không
còn consumer. Natural Language Query có owner duy nhất là `nlq`.

### Quyết định

- Natural Language Query thuộc `nlq`.
- Capability AI khác chỉ tạo module riêng khi có use case và ownership rõ ràng.
- Không dùng `ai_engine` làm thư mục tổng hợp mọi logic có chữ “AI”.

## Câu hỏi 5: `nlq` làm gì?

### Trả lời

`nlq` là canonical owner của Natural Language Query:

```text
Question
-> Intent parsing
-> Dynamic Gold schema retrieval
-> SQL generation
-> Read-only validation
-> Gold query execution
-> Response and query-history event
```

`nlq` không sở hữu dashboard query, data transformation hoặc domain schema. Schema context phải lấy động từ Registry/Governance và về sau phải lọc theo published state, freshness và access control.

## Câu hỏi 6: `query` làm gì?

### Trả lời

`query` không phải một capability độc lập và đã được xóa sau khi xác nhận không còn consumer.

### Quyết định

- Dashboard/KPI read APIs thuộc `dashboard`.
- Natural-language query và SQL execution thuộc `nlq`.
- Query audit thuộc `query_history`.
- Chỉ tạo một generic query module mới nếu sau này xuất hiện use case riêng không thuộc ba owner trên.

Như vậy không còn overlap trách nhiệm giữa `ai_engine`, `nlq` và `query`:

```text
ai_engine -> removed
query     -> removed
nlq       -> canonical NLQ capability
dashboard -> canonical KPI/read capability
```

## Câu hỏi 7: `core/database.py` có còn cần không?

### Trả lời

Không. File đã được xóa; database infrastructure thuộc `infra/db/`.

### Quyết định

- SQLAlchemy `Base` nằm tại `infra/db/base.py`.
- Engine, session factory và `get_db` nằm tại `infra/db/session.py`.
- Module ORM models có thể dùng persistence base từ infrastructure layer vì model/repository thuộc
  persistence adapter, không phải domain core.
- Alembic và model imports đã được migrate sang canonical base.
- `core/deps.py` không còn import DB infrastructure.
- Session/repository wiring mới phải đặt tại delivery/composition boundary.

## Câu hỏi 8: Mọi module có cần cùng cấu trúc file không?

### Trả lời

Không. Boundary quan trọng hơn hình thức folder.

Một module chỉ tạo file khi có capability tương ứng:

```text
router.py      # HTTP adapter
schemas.py     # HTTP request/response
service.py     # Application use case
contracts.py   # Public DTO/port/event
repository.py  # Persistence implementation
models.py      # ORM models
```

Không tạo file rỗng chỉ để module nhìn giống nhau. Tuy nhiên, khi các concern cùng tồn tại thì phải giữ hướng
 `router -> service -> contract/port -> adapter/repository`.

## Câu hỏi 9: Architecture test cần kiểm tra gì?

### Quyết định

Architecture tests phải kiểm tra tối thiểu:

- `domains/<domain>` không import `modules`, FastAPI hoặc `infra`.
- `core` và `shared` không import feature modules hoặc concrete domains.
- Generic ingestion, pipeline, governance và NLQ không import concrete domain.
- Module không import router, repository hoặc models của module khác.
- Infrastructure không import module service; chỉ được import public port/contract.
- Router không được dùng như internal service.
- Không có circular dependency giữa module packages.
- Không có legacy router/model import exception.

### Trạng thái hiện tại

Architecture tests hiện quét AST toàn bộ `app/`, enforce low-level package isolation,
concrete-domain isolation, public cross-module contracts, infrastructure direction, router
isolation và cycle detection.

Documentation không thay thế automated enforcement. Rule đã chốt phải được đưa vào CI để coupling sai không quay trở lại.

## Câu hỏi 10: Các subtasks nên được chốt thế nào?

| Subtask | Output mong đợi | Trạng thái hiện tại |
| :--- | :--- | :---: |
| Define Module Responsibilities | Ownership, input/output và non-responsibility | Done - documented |
| Define Module Communication | Sync call, command, event và read contract | Done - documented |
| Define Dependency Direction | Import layers, matrix và exceptions | Done - documented |
| Define Shared Contracts/Interfaces | Port/DTO/event cho use case thực tế | Done - ingestion, pipeline, NLQ |
| Review Cross-module Dependencies | Danh sách vi phạm và migration plan | Done ở mức foundation review |
| Refactor Direct Dependencies | Loại bỏ compatibility/direct adapter dependency | Done |
| Standardize Internal Structure | Convention, không ép file rỗng | Done - documented |
| Add Architecture Tests | Enforce toàn bộ dependency rules | Done |
| Regression Verification | Ruff, Mypy, Pytest, architecture tests | Done |

Module Boundary implementation có thể chuyển Done vì documentation, refactor và automated
enforcement đều đã hoàn thành trong phạm vi codebase foundation.

## Câu hỏi 11: Acceptance Criteria cụ thể là gì?

### AC1 - Module ownership

Mỗi module chính có responsibility, input/output, data ownership và non-responsibility được document rõ ràng.

### AC2 - Cross-module communication

Cross-module communication dùng public service, command, event hoặc read contract đã xác định; module không truy cập implementation nội bộ của module khác.

### AC3 - Dependency direction

Dependency direction giữa `core`, `shared`, `infra`, `domains`, `modules` và composition root được xác định và kiểm tra tự động.

### AC4 - No circular dependency

Không tồn tại circular dependency giữa các module chính.

### AC5 - Replaceable implementation

Thay implementation của một module hoặc infrastructure adapter không yêu cầu sửa consumer khi public contract không thay đổi.

### AC6 - Regression safety

Ruff, Mypy, Pytest và architecture tests vẫn pass sau refactor.

### AC7 - Team documentation

Có Module Responsibility, Communication và Dependency Direction guides làm chuẩn chung cho team.

### AC8 - NFR07

Các module có ranh giới trách nhiệm rõ ràng, dependency có kiểm soát và coupling đủ thấp để phát
triển độc lập trong Modular Monolith.

## Câu hỏi 12: Definition of Done của task là gì?

Task chỉ Done toàn phần khi có đủ:

```text
Code
├── public contracts/ports cần thiết
├── service boundaries rõ
├── direct dependency violations đã refactor
└── architecture tests enforce dependency rules

Docs
├── backend-module-responsibilities.md
├── backend-module-communication.md
├── backend-dependency-direction.md
└── module-boundary-questions-and-decisions.md

Verification
├── Ruff pass
├── Mypy pass
├── Pytest pass
├── architecture tests pass
└── không có circular dependency
```

## Đánh giá codebase hiện tại

### Đã đạt

- Trách nhiệm từng module đã được chốt.
- Communication patterns đã được chốt.
- Dependency direction và import matrix đã được chốt.
- Domain Registry và generic domain contracts đã tồn tại.
- Pipeline đã có ports/contracts nền tảng.
- Ingestion và NLQ đã có public ports/contracts.
- Architecture tests enforce dependency direction và circular dependency rule.
- `ingest` ambiguity đã được loại bỏ; `ingestion` là canonical module.
- NLQ adapters được inject tại composition provider.
- Database compatibility facade và legacy aliases đã được xóa.

### Ngoài phạm vi hiện tại

- Tạo typed command/event contracts khi workflow background/persistence được triển khai.
- Thay placeholder adapters bằng production MinIO/Delta/PostgreSQL/LLM implementations.
- Bổ sung contract mới khi có use case thật; không tạo interface rỗng trước nhu cầu.

## Kết luận

Câu trả lời kiến trúc được chốt như sau:

- `nlq` là canonical AI query module.
- `ai_engine` và `query` đã được xóa.
- Database infrastructure thuộc `infra/db`; `core/database.py` đã được xóa.
- Module giao tiếp qua public service, command, typed event hoặc read contract.
- Dependency luôn hướng vào stable contract; infrastructure implement application-owned ports.
- Không ép mọi module có cùng file tree.
- Known dependency violations đã được refactor và architecture tests bảo vệ boundary tự động; User
  Story đạt Definition of Done ở mức codebase foundation.
