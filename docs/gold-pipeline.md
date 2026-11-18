# Luồng Silver → Gold

Gold tạo dữ liệu tổng hợp phục vụ báo cáo từ **Silver đã công bố**. Bản đầu hỗ trợ Nhân sự, Đào tạo và Tuyển sinh; tự tạo việc Gold khi bản Silver hiện hành thay đổi. Dữ liệu không đạt chuẩn vẫn nằm ở hồ sơ cách ly, không được đưa vào phép tổng hợp.

## Luồng hoạt động

1. STAFF nạp file. Ingestion kiểm tra, lưu Bronze và ghi kết quả tiếp nhận.
2. Silver được xử lý thủ công hoặc bởi Silver Worker. Bronze → Silver mặc định tự chạy, không cần chọn dataset/profile theo [hướng dẫn Auto Silver](auto-silver-pipeline.md).
3. Cuối giao dịch công bố Silver, PostgreSQL tự tạo một việc Gold. Nếu công bố Silver bị hủy, việc Gold cũng không được tạo. Không cần gọi thêm API Gold cho mỗi file mới.
4. Gold Worker nhận việc từ PostgreSQL, giữ cố định phiên bản Silver nguồn và phiên bản quy định tổng hợp.
5. Worker kiểm tra bản kê Silver, mã kiểm tra nội dung, phiên bản object, số dòng và nguồn gốc của từng bảng. Bản thử, nguồn không rõ hoặc phạm vi trộn nhiều bộ dữ liệu/lĩnh vực sẽ bị từ chối.
6. DuckDB tổng hợp dữ liệu, giữ nguyên mã như `007` và `7`. Giá trị trống được ghi nhận thành nhóm thiếu thông tin; không tự điền hoặc đoán giá trị.
7. Ghi Parquet và bản kê Gold vào MinIO, đọc lại để kiểm tra. Sau đó, trong cùng giao dịch PostgreSQL: lưu số liệu báo cáo, chuyển bản Gold hiện hành, hoàn tất lượt xử lý và ghi nguồn gốc Silver → Gold.
8. Sau công bố Gold, `gold_sync_job` xếp hàng tự động. Worker tạo Parquet Gold chi tiết dưới `gold/production/<scopehash>/<job_id>/business/`, đọc lại và kiểm chứng rồi mới upsert vào các bảng nghiệp vụ trong một transaction. Bản Gold đã bị thay thế không được ghi DB. Bước này độc lập với tổng hợp, nên phải xem thêm `database_sync_status`.
9. UI đọc số liệu hiện hành, theo dõi lịch sử, trạng thái ghi DB và tải báo cáo CSV. File hoặc lần chạy còn dang dở không được coi là Gold đã công bố.

## Gold bản đầu tổng hợp gì?

Quy định hiện tại: `group-counts-v1`, có mã kiểm tra cấu hình trong bản kê và bằng chứng nguồn gốc.

| Lĩnh vực | Loại dữ liệu đã nhận diện | Tổng hợp |
| --- | --- | --- |
| Nhân sự | employee | Số bản ghi; nhóm theo giới tính, trạng thái nhân viên, năm sinh nếu có |
| Nhân sự | assignment, lecturer | Số bản ghi; nhóm theo đơn vị, vị trí, trạng thái phân công, học vị, học hàm nếu có |
| Đào tạo | student | Số bản ghi; nhóm theo giới tính, trạng thái, chương trình, năm tuyển sinh nếu có |
| Đào tạo | class, enrollment, semester, course | Số bản ghi; nhóm theo học phần, học kỳ, lớp, trạng thái và năm học tương ứng |
| Tuyển sinh | applicant, admission | Số bản ghi; nhóm theo giới tính, tỉnh thành, chương trình, năm, phương thức, trạng thái tương ứng |
| Tuyển sinh | program, major | Số bản ghi; nhóm theo ngành, bậc học, đơn vị, trạng thái tương ứng |
| Cả ba | File có cấu trúc riêng, ví dụ `file_4` | Chỉ tổng số bản ghi; không đoán loại nghiệp vụ từ tên file |

Mỗi phép nhóm được tính riêng theo một cột. Cột không có trong Silver được bỏ qua, không tạo số liệu giả. `record_count` là số bản ghi Silver trong từng nguồn, **không mặc định là số người duy nhất toàn trường**. Các nguồn/bộ dữ liệu giữ riêng; không tự cộng các file có thể chứa cùng một người.

Các công thức lương, điểm trung bình, tỷ lệ trúng tuyển, chỉ tiêu đào tạo và dashboard nghiệp vụ chưa nằm trong bản này. Cần chốt công thức, kỳ báo cáo, liên kết dữ liệu và cách xử lý bản ghi trùng trước khi thêm phiên bản tổng hợp mới. Tầng NLQ hiện tại chưa được chuyển thành truy vấn Gold thật trong thay đổi này.

## Lưu ở đâu?

| Nơi lưu | Nội dung |
| --- | --- |
| MinIO bucket cấu hình `MINIO_BUCKET_GOLD` | `gold/production/<mã phạm vi>/<job_id>/aggregate-<checksum>.parquet` và `manifest-<checksum>.json` |
| `app.gold_scope` | Phiên bản Silver cần xử lý, thế hệ cập nhật và bản Gold đang sử dụng |
| `app.gold_job` | Lịch sử việc Gold, phiên bản nguồn, lĩnh vực/bộ dữ liệu được giữ cố định, lỗi và bản kê |
| `app.gold_attempt` | Lịch sử từng lần nhận việc; chạy lại không xóa lần cũ |
| `app.gold_worker` | Tín hiệu hoạt động của bộ xử lý |
| `gold.aggregate` | Số liệu có phiên bản để xem lịch sử hoặc xuất CSV |
| `gold.current_metrics` | Chỉ số liệu của Gold tương ứng Silver hiện hành |
| `gold.hr_summary`, `gold.academic_summary`, `gold.admissions_summary` | Các khung nhìn theo lĩnh vực |
| `app.pipeline_lineage` | Bằng chứng Silver → Gold, liên kết tiếp với nguồn gốc Bronze → Silver |

Khi Silver đổi mà Gold chưa xử lý xong, số liệu cũ không được phục vụ qua các khung nhìn hiện hành. API `/sources` cho biết đang chờ, đang xử lý hay bị lỗi. Lịch sử vẫn tải được theo `job_id`; CSV đó là báo cáo của phiên bản đã chọn, không mặc định là mới nhất.

## API cho FE

Tất cả đường dẫn dưới đây có tiền tố `/api/v1`. Dùng Bearer token đăng nhập hiện tại.

| API | Cách dùng |
| --- | --- |
| `GET /pipeline/gold/recipes` | Xem phiên bản quy định và các phép nhóm được hỗ trợ |
| `GET /pipeline/gold/sources?domain=hr&limit=20&offset=0` | Silver hiện hành, tên bộ dữ liệu, lượt Gold và tiến độ đồng bộ |
| `POST /pipeline/gold/refresh` | Khởi tạo Gold cho Silver đã có từ trước; body `{"silver_snapshot_id":"<UUID>"}`; trả 202. Gọi lại không tạo trùng |
| `GET /pipeline/gold/jobs?domain=hr&dataset_id=4&status=FAILED&limit=20&offset=0` | Lịch sử; các bộ lọc là tùy chọn |
| `GET /pipeline/gold/jobs/{job_id}` | Chi tiết, bản kê, Silver nguồn, quy định, kết quả và bằng chứng truy vết |
| `GET /pipeline/gold/jobs/{job_id}/attempts` | Lịch sử từng lần xử lý |
| `POST /pipeline/gold/jobs/{job_id}/retry` | DATA_ADMIN/ADMIN yêu cầu chạy lại lượt lỗi của Silver còn hiện hành |
| `GET /pipeline/gold/metrics?domain=hr&dataset_id=4&limit=100&offset=0` | Số liệu Gold hiện hành, phân trang tối đa 1000 dòng/lần |
| `GET /pipeline/gold/jobs/{job_id}/report` | Tải toàn bộ số liệu của phiên bản dưới dạng CSV UTF-8 |
| `GET /pipeline/gold/workers` | DATA_ADMIN/ADMIN xem thời điểm bộ xử lý còn hoạt động |

Nhân viên và quản lý chỉ đọc Gold của lĩnh vực được xác định từ vai trò trên máy chủ. Với lĩnh vực của manager, backend dùng role để xác định mã phòng phụ trách đang hoạt động và kiểm tra `owner_department_id` của dataset; dataset chưa gán phòng không được hiển thị. Gửi `domain` khác không mở rộng quyền. ADMIN, DATA_ADMIN và VIEWER được xem số liệu tổng hợp ba lĩnh vực; chỉ ADMIN/DATA_ADMIN tạo việc/chạy lại/xem worker. Các API đọc không trả token giữ quyền xử lý. Màn hình manager dùng [API Manager](api/manager-api.md) cho tổng quan, biểu đồ và yêu cầu sửa dữ liệu.

UI quản trị nằm ở `/data/gold`, có lọc lĩnh vực, phân trang, lịch sử, chi tiết nguồn Silver, xác nhận chạy lại, tải CSV và trạng thái worker. API đọc đã sẵn cho các màn hình báo cáo phòng ban; chưa thay thế toàn bộ dashboard của từng vai trò.

## Chạy local

Chạy tại thư mục backend, sau khi PostgreSQL và MinIO hoạt động:

```bash
make db-gold-upgrade
.venv/bin/python -m app.infra.minio.gold_accounts prepare
.venv/bin/python -m app.infra.minio.gold_accounts apply
docker compose up -d --build gold-worker
```

`prepare` chỉ chạy một lần để tạo `.env.gold-worker.local`, quyền file 0600; không đưa file này lên Git. Nếu đã có file, bỏ qua bước `prepare`. `apply` cấp hoặc đồng bộ quyền cho chính tài khoản đã chuẩn bị, không xuất mật khẩu. Tài khoản chỉ đọc `silver/production/*` và ghi/đọc `gold/production/*`; không được đọc Bronze hoặc xóa object.

`make dev` tiếp tục chạy API trong Terminal và các worker trong Docker, nay có thêm `gold-worker`. Chạy worker riêng ngoài Docker bằng `make gold-worker`. Database mới tự áp dụng chuỗi đầy đủ qua `infra/postgres/init/04-auto-pipeline.sh` và `migrations/upgrade.psql`; database đang có dùng `make db-upgrade` để bảo đảm đủ nền Silver trước Gold. `make db-gold-upgrade` chỉ dành cho DB đã có đủ nền Silver.

```bash
docker compose logs -f gold-worker
```

Migration không tự xử lý hàng loạt Silver cũ. Chọn nguồn trong trang Gold và bấm **Tạo Gold** một lần nếu trạng thái là **Chưa tạo Gold**. Các lần công bố Silver sau đó tự tạo việc.

## Khi gặp lỗi

Lượt xử lý có thời hạn 120 giây, gia hạn mỗi 10 giây. Lỗi kết nối tạm thời được thử tối đa 3 lần, chờ 30 rồi 60 giây; lỗi dữ liệu, thiếu quyền hoặc sai cấu hình cần sửa nguyên nhân trước. Mỗi lần chạy lại thủ công thêm tối đa 3 cơ hội và giữ toàn bộ lịch sử. Gold mới chỉ công bố nếu Silver nguồn vẫn hiện hành và worker vẫn còn quyền. Worker cũ hết thời hạn hoặc lượt Gold của phiên bản Silver cũ không được ghi đè bản mới.

Giới hạn ban đầu: tối đa 1.000.000 bản ghi Silver, 200 MiB Parquet đầu vào, 100.000 nhóm cho mỗi phép nhóm. Phạm vi Silver chứa dữ liệu nhiều dataset hoặc nhiều lĩnh vực bị từ chối bằng `GOLD_MIXED_SOURCE_SCOPE_UNSUPPORTED` để tránh tổng hợp nhầm; cần thiết kế mô hình hợp nhất riêng trước khi hỗ trợ. Tệp Gold dang dở có thể còn trên kho nhưng không được đọc như bản đã công bố; cơ chế thu gom các tệp này chưa nằm trong thay đổi.

## Kiểm chứng

```bash
.venv/bin/pytest tests/test_gold_pipeline.py --no-cov
GOLD_LIVE_TEST=1 .venv/bin/pytest tests/test_gold_live.py --no-cov
```

Kiểm thử hạ tầng tạo database riêng và chỉ dọn object của chính lượt thử: tải file thật vào Bronze, chạy Silver Worker, tự tạo Gold, chạy Gold Worker, đọc lại Parquet/số liệu PostgreSQL, tải CSV, kiểm tra phạm vi STAFF, hủy giao dịch, đổi/quay lại Silver, worker hết thời hạn và chạy lại không mất lịch sử.

## Đồng bộ dữ liệu chi tiết vào PostgreSQL

`gold-business-records-v1` hỗ trợ các bảng nghiệp vụ đã liệt kê trong `gold_business_sync_repo.MODELS`, gồm chức vụ, nhân viên, phân công, học phần, chương trình, sinh viên và các quan hệ liên quan. Riêng phòng ban chỉ được ánh xạ tới danh mục hệ thống PDT/PNS/PTS đã có, không được thêm/cập nhật từ nguồn. Không cho phép ghi bảng tài khoản, vai trò hoặc quyền. Bảng `app.gold_record_identity` ánh xạ namespace nguồn + loại thực thể + khóa nguồn sang PK thật của ứng dụng; mã `007` khác `7`, không dùng ID nguồn tùy ý làm FK DB. Mã nghiệp vụ trùng giữa hai nguồn đã quản lý bị chặn để tránh ghi đè nhầm hồ sơ.

Silver phục vụ phân tích tiếp tục loại các trường cá nhân theo hợp đồng. Với employee/student/applicant mới xử lý, họ tên hợp lệ từ đúng Bronze gốc được giữ trong object chi tiết nghiệp vụ riêng; bản kê chỉ dẫn object này cho Gold. Dữ liệu phân tích/xem trước không tự thêm họ tên. Không suy ra ngày sinh từ năm sinh, không tự đặt tên hoặc tạo chức vụ còn thiếu.

Trạng thái đồng bộ: QUEUED, RUNNING, SYNCED, BLOCKED, FAILED, SUPERSEDED. `BLOCKED` trả mã lỗi, thực thể và trường bị thiếu, không trả bản ghi cá nhân. Sau khi bổ sung dữ liệu liên kết cùng namespace, các lượt BLOCKED cùng lĩnh vực được xếp lại tự động. Data Admin cũng có thể gọi `POST /api/v1/pipeline/gold/jobs/{job_id}/database-sync/retry` cho bản Gold hiện hành đang BLOCKED/FAILED. API jobs/sources trả `database_sync_status` và `database_sync_error`; chi tiết trả thêm `database_sync_report` và số dòng theo bảng trong `database_sync_result`.

Đồng bộ là upsert, không xóa hồ sơ chỉ vì vắng trong một file. File không nhận diện được thực thể cần ánh xạ; riêng cấu trúc ghi danh phẳng đầy đủ đã khai báo được tách danh mục học phần, không tự tạo sinh viên/lớp chưa đủ dữ liệu. Dữ liệu nghiệp vụ ở DB là kết quả sau Gold; dữ liệu Silver và toàn bộ metadata Silver nằm trên MinIO.

### Phòng hệ thống và đơn vị trong dữ liệu nguồn

`app.department` là danh mục phân quyền cố định gồm `PDT`, `PNS`, `PTS`; đây không phải bảng nhập cây tổ chức của file nguồn. Gold business sync chỉ ánh xạ bản ghi nguồn có đúng mã tới phòng đã tồn tại và đang ACTIVE. Không thêm phòng, ghi đè tên/trạng thái/loại phòng hoặc thay quan hệ phòng cha từ dữ liệu nạp.

Mã trường, khoa, bộ môn hoặc mã phòng khác vẫn được lưu trong Bronze/Silver/Gold, nhưng không tự đồng bộ vào `app.department`. Bước đồng bộ chi tiết PostgreSQL trả `BLOCKED / GOLD_SYNC_SYSTEM_DEPARTMENT_ONLY` khi gặp mã ngoài danh mục, thay vì tự gán vào một trong ba phòng. Nếu cần khai thác các đơn vị nguồn ở PostgreSQL, phải thiết kế danh mục tổ chức nghiệp vụ riêng; không dùng chung danh mục RBAC.

`GOLD_SYNC_SYSTEM_DEPARTMENT_MISSING` báo phòng chuẩn chưa cấu hình/không ACTIVE. `GOLD_SYNC_DEPARTMENT_MAPPING_CONFLICT` chặn việc tái dùng ID nguồn đã ánh xạ để đổi sang phòng khác. Hai nguồn được phép cùng ánh xạ tới một phòng chuẩn nhưng không được sửa phòng đó.

Migration `20261118_system_departments.sql` thêm CHECK ở PostgreSQL để chặn mã khác trên mọi đường ghi. Migration dừng nếu database cũ còn phòng ngoài danh mục, không tự xóa hoặc thay đổi các liên kết nghiệp vụ cũ.
