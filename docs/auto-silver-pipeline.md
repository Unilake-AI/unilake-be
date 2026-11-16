# Luồng tự động Bronze → Silver

Ngày triển khai và kiểm chứng local: **15/11/2026**.

## 1. Phạm vi đã triển khai

| Mục | Kết quả |
| --- | --- |
| DUCK-03 | Chuẩn hóa cấu trúc, kiểu dữ liệu, định danh và nhãn chạy qua `DuckDBStandardizationEngine`; SQL tìm xung đột khóa chuẩn toàn lô |
| AUTO-01 | Cấu hình có phiên bản theo dataset; hàng đợi, trạng thái và lịch sử từng lần thử lưu PostgreSQL |
| AUTO-02 | Trigger tạo đúng một job Silver trong cùng giao dịch chuyển Ingestion sang `SUCCESS`, khi dataset đã bật tự chạy |
| AUTO-03 | Silver Worker riêng, nhận việc có khóa, gia hạn, phục hồi việc hết hạn và Docker healthcheck |
| AUTO-04 | Dùng chung Quarantine, kiểm tra phê duyệt, kiểm tra công bố một phần và Silver Publication; retry có giới hạn |
| AUTO-05 | Kiểm thử thật bằng PostgreSQL + MinIO local, bao gồm worker chạy bằng một tiến trình riêng |

**DuckDB và Python cùng tham gia xử lý.** SQL điều phối kiểm tra từng dòng, lưu kết quả và tìm nhóm khóa chuẩn trùng nhau. Những quy tắc chuyển kiểu, ánh xạ cấu trúc và đối chiếu mã đã được kiểm chứng vẫn dùng hàm Python qua UDF. Đây chưa phải bộ quy tắc viết hoàn toàn bằng SQL thuần.

Bảng đối chiếu mã chỉ đọc và dấu kiểm chứng được chuẩn bị một lần cho cả lô, tránh dựng lại theo từng dòng. Mã nguồn như `"007"` không bị tự đổi thành `7`; mã nội bộ chỉ xuất hiện sau đối chiếu rõ ràng.

## 2. Luồng vận hành

1. Quản trị thiết lập và kiểm tra cách xử lý gắn đúng nguồn, dataset, loại tệp và cấu trúc cột. Chế độ giữ mã nguồn không cần bảng đối chiếu; chế độ hợp nhất mã cần chuẩn bị bản đối chiếu đã chọn. Kiểm tra phê duyệt vẫn áp dụng nếu môi trường yêu cầu.
2. Quản trị bật tự chạy cho dataset bằng API bên dưới. Mặc định dataset chưa có cấu hình sẽ không tự chạy.
3. STAFF tải file lên qua API Ingestion hiện có. Quyền phòng ban được kiểm tra như trước.
4. Ingestion Worker kiểm tra file, lưu và xác minh đúng phiên bản Bronze.
5. Trong giao dịch ghi `Ingestion SUCCESS`, PostgreSQL tự tạo Auto Pipeline Job nếu dataset đã bật. Nếu không thể tạo job, giao dịch thành công đó cũng không được ghi nhận. Không có khoảng trống giữa hai lần commit.
6. Job giữ bản cấu hình, người cấu hình, phiên bản cấu hình, fingerprint và toàn bộ bằng chứng Bronze của thời điểm tiếp nhận. Không tự lấy phiên bản file mới hơn khi thử lại.
7. Silver Worker lấy việc từ PostgreSQL bằng `FOR UPDATE SKIP LOCKED`. Mỗi dataset chỉ có một job tự chạy đang ở trạng thái `RUNNING`.
8. Worker chạy Cleaning → Standardization → lưu vấn đề và dòng cách ly → kiểm tra điều kiện công bố → Parquet Silver.
9. Trước khi công bố, worker phải còn đúng `lease_token` và quyền xử lý chưa hết hạn. Trạng thái job thành công được ghi trong chính giao dịch công bố Silver.
10. Worker liên lạc/gia hạn qua kết nối riêng trong lúc xử lý dữ liệu. Khi không có việc, worker vẫn cập nhật heartbeat để có thể giám sát.

Bật tự chạy chỉ áp dụng cho các lần Ingestion chuyển sang `SUCCESS` sau đó. Không tự chạy lại toàn bộ dữ liệu Bronze cũ. Với dữ liệu cũ, có thể dùng API Cleaning thủ công đang có.

Một profile vẫn gắn với `source_table`, loại tệp và cấu trúc nguồn cụ thể. Upload tệp khác cấu trúc hoặc tên không khớp quy định sẽ được báo lỗi; hệ thống không tự đoán một bộ quy định thay thế.

## 3. Cơ sở dữ liệu và quyền truy cập

| Bảng | Mục đích |
| --- | --- |
| `app.auto_pipeline_policy` | Một cấu hình theo dataset, phiên bản tăng khi cập nhật |
| `app.auto_pipeline_job` | Một job theo Ingestion thành công, có bản cấu hình và bằng chứng Bronze riêng |
| `app.auto_pipeline_attempt` | Lịch sử từng lượt nhận việc, lỗi, worker và thời gian kết thúc |
| `app.silver_worker_heartbeat` | Thời điểm worker còn liên lạc được PostgreSQL |
| `app.cleaning_run.auto_job_id` | Liên kết lượt làm sạch với job tự động |

Không dùng Redis để xếp hàng Silver.

- `ADMIN`, `DATA_ADMIN`: cấu hình, theo dõi, xem worker và yêu cầu thử lại.
- `HR_STAFF`, `ACADEMIC_STAFF`, `ADMISSIONS_STAFF`: xem job/kết quả/lịch sử trong domain được cấp quyền; không đổi cấu hình hoặc yêu cầu retry.
- `VIEWER`: không được dùng các API này.
- API đọc job không trả quyền giữ việc, cấu hình nội bộ hoặc bằng chứng truy cập kho lưu trữ cho STAFF.

## 4. API mới trên Swagger

### Gọi API từ giao diện

Đã kết nối FE tại `/data/auto-pipeline` và thêm mục **Xử lý Silver tự động**
trong menu quản trị dữ liệu. Trang này gọi đủ các API dưới đây:

- Chọn bộ dữ liệu bằng tên, đọc cấu hình đang lưu; chưa có cấu hình được hiển
  thị là chưa bật, không coi lỗi kết nối hoặc API bị thiếu là cấu hình trống.
- Danh sách cách xử lý lấy từ `GET /pipeline/profiles?dataset_id=...`; backend
  lọc theo bộ dữ liệu trước khi chia trang. Có thể tải thêm khi danh sách dài.
- Quản trị chọn cách xử lý, bật/tắt, chọn cách công bố một phần, số lần thử và
  thời gian chờ. Chỉ bấm **Lưu cấu hình tự chạy** mới gọi PUT; gửi đúng phiên
  bản vừa đọc. Nếu có người sửa đồng thời, hiển thị lỗi và tải lại bản mới,
  không tự ghi đè. Cấu hình cũ vẫn tắt được khi profile đã rời danh sách.
- Danh sách lượt chạy lọc theo bộ dữ liệu/lần nạp Bronze, phân trang; mở chi
  tiết sẽ gọi API kết quả và lịch sử thử lại. Retry chỉ xuất hiện với lượt
  thất bại, cần xác nhận; backend kiểm tra cấu hình vẫn bật và cùng phiên bản.
- Trạng thái job và thời điểm liên lạc của Silver Worker được cập nhật mỗi
  5 giây khi bật tự làm mới. Không suy đoán worker còn hoạt động chỉ vì còn
  bản ghi heartbeat cũ.

Trong chi tiết lần nạp thành công ở `/data/ingestion` và `/staff/ingestion`,
phần **Xử lý Silver tự động** đọc job theo `ingestion_job_id`, rồi đọc kết quả
và lịch sử của đúng job đó. Hiển thị riêng kết quả công bố, kể cả công bố một
phần, số dòng cách ly và lỗi. Các role STAFF dùng chung phần theo dõi chỉ đọc,
không gọi API cấu hình, retry hay danh sách worker. Kết quả Bronze thành công
không được dùng thay cho kết quả Silver.

Mặc định chưa bật cấu hình cho bộ dữ liệu của người dùng. Việc nối giao diện
không tự nạp lại Bronze cũ, không tự bật chính sách và không tự duyệt gửi các
hồ sơ cách ly về người nạp.

Tất cả đường dẫn dưới đây có tiền tố `/api/v1` và yêu cầu đăng nhập.

| Phương thức | Đường dẫn | Cách dùng |
| --- | --- | --- |
| `PUT` | `/pipeline/auto/datasets/{dataset_id}` | Tạo/cập nhật cấu hình tự chạy |
| `GET` | `/pipeline/auto/datasets/{dataset_id}` | Xem phiên bản và cấu hình hiện tại |
| `GET` | `/pipeline/auto/jobs` | Danh sách job; lọc `dataset_id`, `ingestion_job_id`; phân trang `limit`, `offset` |
| `GET` | `/pipeline/auto/jobs/{job_id}` | Trạng thái và kết quả |
| `GET` | `/pipeline/auto/jobs/{job_id}/attempts` | Lịch sử từng lần thử |
| `POST` | `/pipeline/auto/jobs/{job_id}/retry` | Yêu cầu thêm một lượt thử cho job `FAILED` |
| `GET` | `/pipeline/auto/workers` | Danh sách heartbeat của Silver Worker |

Ví dụ bật tự chạy cho dataset đã có profile `hr-positions`:

```json
{
  "expected_revision": 0,
  "enabled": true,
  "profile_id": "hr-positions",
  "allow_partial_publication": false,
  "max_attempts": 3,
  "retry_delay_seconds": 30
}
```

- `expected_revision=0`: tạo cấu hình lần đầu. Khi sửa, gửi revision đang được `GET` trả về. Bản cũ bị sửa đồng thời sẽ nhận HTTP 409.
- Tên profile trong ví dụ phải được thay bằng profile có thật, khớp dataset. Đây không phải cấu hình mặc định được hệ thống tự cài.
- `max_attempts`: từ 1 đến 10; tính cả lần chạy đầu và các lần phục hồi quyền xử lý đã hết hạn.
- `retry_delay_seconds`: từ 1 đến 3.600 giây; thời gian chờ tăng gấp đôi ở mỗi lần lỗi, tối đa 3.600 giây.
- `allow_partial_publication=false`: có dòng bị loại thì dừng ở trạng thái `QUARANTINED`. Nếu bật, vẫn phải đạt kiểm tra an toàn khóa và phê duyệt công bố một phần khi production yêu cầu.

Đổi cấu hình hoặc tắt tự chạy sẽ hủy các job cũ còn `QUEUED`/`RETRY_WAIT` khi worker thăm dò tiếp theo. Job đã `RUNNING` tiếp tục dùng bản cấu hình đã chốt, đồng thời vẫn phải qua kiểm tra nguồn và kiểm tra phê duyệt khi công bố. Không tự đổi quy định giữa một lượt đang chạy.

## 5. Trạng thái, retry và phục hồi

| Trạng thái | Ý nghĩa |
| --- | --- |
| `QUEUED` | Đã tạo việc, chờ worker |
| `RUNNING` | Worker đang giữ quyền xử lý |
| `RETRY_WAIT` | Lỗi tạm thời, đang chờ đến thời điểm thử lại |
| `SUCCESS` | Đã công bố Silver; xem thêm trạng thái publication trong `result` để biết có dòng cách ly không |
| `QUARANTINED` | Không đủ điều kiện công bố hoặc không có dòng đạt; không tự retry |
| `FAILED` | Lỗi không thể tự sửa, hoặc đã hết số lượt thử |
| `CANCELLED` | Cấu hình đã tắt/thay đổi trước khi job được nhận lại |

Chỉ lỗi kết nối, thời gian chờ, hạ tầng tạm thời và xung đột phiên bản Silver hiện hành được tự retry. Lỗi dữ liệu, checksum, đối chiếu mã, quyền truy cập, thiếu file/bucket, thiếu bảng/cột hoặc cấu hình không hợp lệ không được tự retry.

Retry thủ công chỉ áp dụng cho `FAILED` và cấu hình vẫn bật, cùng revision. Thao tác này cho thêm đúng một lượt, giữ lịch sử và bản Bronze/cấu hình ban đầu; không xóa số lần đã thử. Nếu revision đã đổi, dùng luồng thủ công với profile mới hoặc tạo lần Ingestion mới.

Nếu worker dừng trước công bố, quyền xử lý sẽ hết hạn rồi job được nhận lại với token mới. Worker cũ không thể công bố bằng token hết hạn. Nếu worker dừng sau công bố nhưng trước xác nhận cuối, job đã là `SUCCESS` trong cùng giao dịch nên không bị công bố thêm lần nữa.

File Silver chưa công bố có thể còn trong kho khi một lượt thất bại. Hàng đợi bảo vệ snapshot được công bố, không đồng nghĩa đã có chính sách tự dọn toàn bộ file chưa công bố. File Bronze vẫn được giữ nguyên để thử lại và truy vết.

## 6. Chạy local

Lần triển khai này đã áp dụng migration và dựng/khởi động Silver Worker trên máy local. Tại thời điểm xác nhận, worker Docker ở trạng thái `healthy`; chưa tự bật cấu hình cho dataset của người dùng.

Với database local đang có ở máy khác:

```bash
docker compose up -d postgres minio minio-init
make db-auto-upgrade
docker compose up -d --build silver-worker
```

Lệnh migration đầy đủ nếu không dùng Make:

```bash
docker compose exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -f -' \
  < infra/postgres/migrations/20261115_auto_silver_pipeline.sql
```

Database mới tạo bằng Compose chạy cùng migration qua `infra/postgres/init/04-auto-pipeline.sh`. Database đang có vẫn phải chạy migration cập nhật; không xóa volume.

Sau đó chạy API bằng Terminal như trước:

```bash
make dev
```

Xem worker và log xử lý:

```bash
docker compose ps silver-worker
docker compose logs -f silver-worker
```

Worker lấy quyền đọc Bronze và ghi Silver từ `.env.silver-backend.local` và `.env.source-reader.local`. Kết nối trong Docker dùng `postgres:5432` và `minio:9000`. Không thay quyền runtime bằng tài khoản root.

Thông số tùy chọn:

```dotenv
SILVER_POLL_SECONDS=2
SILVER_HEARTBEAT_SECONDS=10
SILVER_LEASE_SECONDS=120
```

Giữ khoảng heartbeat nhỏ hơn thời gian giữ việc. Mặc định job được gia hạn mỗi 10 giây và quyền xử lý tồn tại tối đa 120 giây kể từ lần gia hạn gần nhất.

## 7. Kiểm chứng và giới hạn

- Có kiểm thử so sánh kết quả chuẩn hóa trên **22 cấu trúc** của Admissions, Academic và HR, bao gồm phiên bản quy định, lỗi, vị trí dòng và thông tin nguồn.
- Bộ kiểm thử local thật gồm **11 trường hợp**: sạch, công bố một phần, toàn bộ lỗi, lỗi tạm thời, dừng sau công bố, hết hạn quyền xử lý, thay cấu hình, nhiều worker, hết số lần retry và retry thủ công, rollback khi tạo job, worker chạy bằng tiến trình riêng, tắt cấu hình khi nguồn đã ngừng hoạt động. Một trường hợp có thể kiểm tra nhiều tình huống liên quan.
- Kiểm thử thật sử dụng API upload CSV, worker Ingestion, PostgreSQL và MinIO thực; đọc lại Parquet Silver. Không thay MinIO bằng thư mục local hoặc PostgreSQL bằng SQLite trong bộ này.
- Mỗi trường hợp dùng database `unilake_auto_test_<uuid>` riêng, phạm vi Silver riêng và job ID ngẫu nhiên lớn để tránh đường dẫn Bronze đang dùng. Cuối test chỉ dọn phiên bản object và database của chính nó.
- Bộ kiểm thử cần thêm tài khoản đọc staging từ `.env.upload-worker.local`, vì một tiến trình kiểm thử thực hiện cả việc của API và Ingestion Worker. Cách nạp này chỉ nằm trong fixture, không cấp quyền đọc staging cho API thường chạy.

Kết quả kiểm chứng:

| Kiểm tra | Kết quả |
| --- | --- |
| Bộ kiểm thử thông thường | **1.475 đạt, 63 bỏ qua**, coverage **85,92%** |
| Bộ kiểm thử hạ tầng thật chạy riêng | **11 đạt**, gồm worker chạy bằng tiến trình riêng |
| Ruff và định dạng | Đạt |
| mypy | Đạt, 182 file nguồn |
| Pyright | 0 lỗi, 0 cảnh báo |
| OpenAPI trong repository | Khớp với Swagger sinh từ code |
| Image Silver Worker | Dựng thành công; service local `healthy` |

63 trường hợp bỏ qua trong lượt chạy thông thường bao gồm 11 kiểm thử auto đã chạy riêng với hạ tầng thật, cùng các kiểm thử live khác và trường hợp không áp dụng cho cấu trúc được chọn. Không coi các trường hợp còn lại đã được kiểm chứng bởi lượt chạy auto này.

Chạy kiểm thử thật sau khi PostgreSQL, MinIO và các tài khoản runtime đã sẵn sàng:

```bash
AUTO_PIPELINE_LIVE_TEST=1 .venv/bin/pytest -q --no-cov tests/test_auto_pipeline_live.py
```

Kiểm tra code và bộ kiểm thử thông thường:

```bash
.venv/bin/pytest -q --cov-report=term:skip-covered
.venv/bin/ruff check app tests
.venv/bin/ruff format app tests --check
.venv/bin/mypy --config-file pyproject.toml app
.venv/bin/pyright --pythonpath .venv/bin/python
.venv/bin/python -m scripts.export_openapi --check
```

Đã kiểm tra riêng chuẩn hóa một lô 20.000 dòng với 20.000 mã nguồn được đối chiếu: 20.000 dòng đạt, khoảng **1,35 giây** trên Python 3.13.7/macOS ARM64. Đây là một phép đo trong bộ nhớ, không gồm upload, đọc/ghi MinIO và công bố Parquet; không dùng làm cam kết hiệu năng cho toàn luồng.

Cấu hình làm sạch vẫn phải được xác nhận rõ ràng. Chế độ `source` giữ mã nguồn và không cần crosswalk; chế độ `canonical` mới yêu cầu bản đối chiếu mã. Tự động hóa ở đây là tự nhận việc và thực thi quy định đã chọn, không tự tạo quy định hoặc tự duyệt dữ liệu. Chưa thực hiện triển khai lên Render hoặc xây giao diện cấu hình FE trong phạm vi này.

## 8. Các file chính để review

- `app/modules/pipeline/standardization/duckdb_engine.py`: thực thi chuẩn hóa và xử lý xung đột qua DuckDB.
- `app/modules/pipeline/repositories/auto_pipeline.py`: hàng đợi, khóa, heartbeat, lịch sử và retry.
- `app/modules/pipeline/services/auto_pipeline.py`: kiểm tra và lưu cấu hình dataset.
- `app/modules/pipeline/services/silver_worker.py`, `app/silver_worker.py`: worker và lệnh khởi chạy.
- `app/modules/pipeline/routers/auto_pipeline.py`: 7 API mới.
- `app/modules/pipeline/services/source_cleaning.py`: chốt phiên bản Bronze/profile và ghi trạng thái thành công cùng giao dịch công bố.
- `app/modules/pipeline/silver/validation.py`: đọc Parquet đúng cả khi JSONB thay đổi thứ tự cột; vẫn kiểm tra kiểu và số dòng.
- `infra/postgres/migrations/20261115_auto_silver_pipeline.sql`: cấu trúc và trigger tạo việc trong giao dịch Ingestion.
- `tests/test_duckdb_standardization_engine.py`, `tests/test_auto_pipeline.py`, `tests/test_auto_pipeline_live.py`, `tests/test_silver_column_order.py`: bằng chứng kiểm thử.
