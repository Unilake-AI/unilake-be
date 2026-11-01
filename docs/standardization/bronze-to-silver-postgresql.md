# Tự động đưa dữ liệu XLSX và JSON từ Bronze vào Silver trên PostgreSQL

**Trạng thái:** Thiết kế đề xuất cho backend UniLake. Các bảng, worker và view được đánh dấu “mới” bên dưới là hạng mục cần triển khai. Trong tài liệu này, Excel nghĩa là file `.xlsx`.

## 1. Kết quả nghiệp vụ

Sau một lần ingest thành công, người dùng có thể theo dõi dữ liệu qua hai mốc riêng. Bronze thành công khi hệ thống đã lưu và xác nhận đúng phiên bản file gốc. Silver thành công khi người dùng có thể truy vấn bằng SQL các bản ghi đã chuẩn hóa của phiên bản vừa công bố. Nếu Silver lỗi, hệ thống giữ file Bronze để xử lý lại và người đọc tiếp tục thấy phiên bản Silver đã công bố trước đó.

Định dạng quyết định cách tách bản ghi. Profile của nguồn quyết định ý nghĩa cột, khóa nghiệp vụ, quy tắc làm sạch và bảng đích. Hai file khác định dạng có thể cùng cấp dữ liệu cho một entity nếu có mapping phù hợp.

## 2. Công nghệ và phần có thể dùng lại

| Thành phần | Vai trò trong thiết kế | Điểm xuất phát trong repo |
|---|---|---|
| MinIO có versioning | Giữ nguyên file Bronze. Worker đọc đúng bucket, key, version và kiểm tra checksum | Compose đã bật versioning cho bucket Bronze |
| PostgreSQL 16 và SQLAlchemy | Lưu hàng đợi bền vững, trạng thái job, snapshot và các bảng Silver có kiểu. Dùng giao dịch để công bố dữ liệu | Ingestion repository đã dùng khóa dòng, lease và heartbeat. Silver đã có run, snapshot và head |
| Python worker dưới Docker Compose | Thêm service `silver-worker` chạy độc lập, lấy việc từ DB và xử lý nền | Mẫu vận hành hiện tại là `python -m app.ingestion_worker` |
| openpyxl và thư viện json của Python | Đọc XLSX và JSON thành cùng dạng bản ghi có vị trí nguồn | Đã có ExcelReader. Cần thêm JSON vào registry, cấu hình nguồn, validation và đường đọc Bronze |
| Pipeline chuẩn hóa hiện tại | Mapping, xử lý giá trị thiếu, kiểu dữ liệu, trùng và định danh theo profile đã duyệt | Các module trong `app/modules/pipeline`. Runner thủ công đang giới hạn ở profile demo |
| FastAPI | Trả trạng thái ingestion, Silver, báo cáo lỗi và cho phép retry có phân quyền | Có endpoint cleaning thủ công cho demo. Cần bổ sung hợp đồng theo dõi job tự động |

PostgreSQL phù hợp để điều phối vì điểm xác nhận ingestion đã nằm trong một transaction SQL. Ghi thêm job Silver ngay tại đó vừa tránh mất yêu cầu khi process chết, vừa tận dụng được cách claim job mà đội đã vận hành. MVP chỉ cần thêm service worker vào Compose. Tần suất poll, kích thước batch, giới hạn file và số worker nên chốt qua đo tải với file thực tế.

### Vai trò của DuckDB khi xử lý Silver

Hiện backend dùng DuckDB trong bộ đọc nguồn CSV và PostgreSQL. XLSX dùng openpyxl. Với thiết kế Silver mới, có thể nhúng DuckDB trong tiến trình `silver-worker` khi cần chạy SQL trên cả lô trước khi ghi PostgreSQL. MVP vẫn có thể dùng các rule Python hiện tại. Nên chọn một phép kiểm tra cụ thể và đối chiếu kết quả với pipeline hiện hữu trước khi chuyển nó sang SQL.

Ví dụ, với profile yêu cầu mỗi mã ngành chỉ xuất hiện một lần trong file, parser nạp vào bảng tạm `stage_major` hai cột `raw_major_id` (VARCHAR) và `source_locator`. Câu SQL sau trả từng vị trí có mã trùng sau khi bỏ khoảng trắng ngoài. Mã thiếu được xử lý bằng rule riêng.

```sql
WITH normalized AS (
    SELECT source_locator, NULLIF(TRIM(raw_major_id), '') AS major_id
    FROM stage_major
), checked AS (
    SELECT *, COUNT(*) OVER (PARTITION BY major_id) AS occurrences
    FROM normalized
)
SELECT source_locator, major_id, occurrences
FROM checked
WHERE major_id IS NOT NULL AND occurrences > 1;
```

Phải kiểm tra trên toàn bộ lô, kể cả khi parser nạp nhiều batch. Worker chuyển các dòng lỗi sang cùng định dạng quality report của pipeline. PostgreSQL vẫn thực thi ràng buộc khóa cuối cùng và transaction publish. Worker đóng kết nối DuckDB sau lần xử lý. Nếu worker chết, worker kế tiếp dựng lại bảng tạm từ đúng Bronze version. Không cần duy trì bản sao Silver bằng file DuckDB.

## 3. Một file đi qua hệ thống như thế nào?

1. **Chọn profile.** Cấu hình nguồn xác định định dạng, dataset, phạm vi dữ liệu, phiên bản mapping và chế độ nạp. Chỉ tự động chạy những nguồn được bật Silver và có profile hợp lệ.
2. **Chụp Bronze.** Ingestion worker xử lý theo chính sách validation Bronze, lưu file gốc, xác nhận object version và checksum. Nếu Bronze bị chặn, trả báo cáo Bronze và bỏ qua việc tạo job Silver.
3. **Giao việc.** Trong nhánh thành công của `IngestionRepository.finish()`, sau khi kiểm tra lease và trước lệnh commit hiện có, thêm job Silver ở trạng thái `QUEUED` cùng tham chiếu Bronze và profile đã ghim phiên bản. Commit chung với cập nhật ingestion thành `SUCCESS`. Bảng job ở đây đóng luôn vai trò hàng đợi. Nếu transaction rollback thì cả hai thay đổi cùng rollback. Việc ghi object MinIO diễn ra trước và nằm ngoài transaction SQL.
4. **Nhận việc.** Silver worker chọn job đến hạn bằng `FOR UPDATE SKIP LOCKED`, cấp lease và token cho lần chạy, đổi trạng thái sang `RUNNING` rồi commit ngay. Worker gia hạn lease trong lúc xử lý. Việc tải và parse file diễn ra ngoài transaction claim để tránh giữ khóa lâu.
5. **Đọc và clean.** Đọc đúng phiên bản Bronze, kiểm tra checksum, tách record kèm vị trí nguồn. Chạy mapping và các rule của profile. Ghi số dòng đọc, hợp lệ, bị loại, mã lỗi và vị trí. Một dòng phát sinh nhiều lỗi vẫn chỉ tính là một dòng bị loại. Chính sách ban đầu giữ theo luồng demo hiện tại: có dòng quarantine thì chặn công bố cả lô.
6. **Chuẩn bị phiên bản.** Ghi theo batch vào vùng staging hoặc phiên bản SQL chưa hiển thị, tách theo run và lần thử. Kiểm tra kiểu, khóa, liên kết và tổng số bản ghi trước khi publish. Mỗi dòng lưu được quan hệ về Bronze và locator. Với dòng hợp nhất từ nhiều nguồn, dùng bảng liên kết nhiều locator.
7. **Công bố.** Trong một transaction PostgreSQL, xác nhận token vẫn thuộc worker, khóa head của scope, kiểm tra snapshot nền, gắn các phiên bản entity vào snapshot, đổi head và đánh dấu job `PUBLISHED`. Một view đọc qua head chỉ nhìn thấy phiên bản hoàn chỉnh sau commit. Các entity không đổi kế thừa phiên bản cũ.
8. **Tra cứu.** API trạng thái trả ingestion job, Silver job, số dòng, report và snapshot nếu đã publish. Người dùng đọc bảng/view Silver bằng quyền đọc. Retry từ Bronze dùng lại object đã ghim.

## 4. Hợp đồng đầu vào cho MVP

| | XLSX | JSON |
|---|---|---|
| Cấu hình | Sheet, dòng header và mapping cột cụ thể cho từng nguồn | Mảng object ở gốc hoặc một đường dẫn danh sách đã cấu hình, ví dụ `data.items` |
| Vị trí lỗi | Tên sheet, số dòng thực trong workbook, tên cột nếu có | Đường dẫn phần tử tính từ 0 và tên trường, ví dụ `$.data.items[4].major_id` |
| Lỗi cần phân biệt | Sai sheet, header trùng, cột bắt buộc thiếu, ô công thức/ô lỗi theo chính sách reader | JSON sai cú pháp, khóa trùng, sai đường dẫn, phần tử không phải object, trường sai kiểu |
| Dữ liệu lồng | Đọc vùng bảng đã cấu hình | Khai báo mapping cho từng đường dẫn được phép. Gặp cấu trúc lạ thì báo lỗi theo profile |

Với JSON, kiểm tra khóa trùng khi parse từng object, trước khi chuyển sang dict. Nếu cần số thập phân chính xác, parse trực tiếp thành Decimal rồi ghi NUMERIC theo schema. Với cả hai định dạng, mã như `0012` phải được profile khai báo là chuỗi. Nếu workbook đã lưu số 12, chỉ khôi phục số 0 đầu khi có quy tắc nghiệp vụ xác định độ dài. Đặt giới hạn dung lượng, số record và kích thước XLSX sau giải nén trước khi bật tự động cho file thực.

## 5. Lưu dữ liệu Silver trong DB

| Thành phần | Thiết kế đề xuất |
|---|---|
| Job xử lý mới | Liên kết ingestion job, Bronze ref, profile và phiên bản rule, scope, khóa chống chạy trùng, trạng thái, attempt, lease, lần retry kế tiếp và báo cáo |
| Bảng entity mới | Ví dụ `silver.major` có các cột nghiệp vụ theo schema và `entity_version_id`. Ràng buộc duy nhất trên phiên bản và khóa nghiệp vụ |
| Snapshot và head | Tái sử dụng khái niệm hiện có. Thêm quan hệ snapshot → entity version để một lần đọc nhìn thấy tập phiên bản nhất quán |
| View SQL mới | Ví dụ `silver.current_major` đi từ head của scope đến phiên bản entity đang có hiệu lực. Kiểm tra bằng truy vấn SQL sau khi publish |
| Lineage và quality report | Gắn record hoặc issue với phiên bản object, locator, quy tắc và run. Giới hạn quyền đọc report chứa dữ liệu nhạy cảm |

**Chế độ nạp phải do chủ dữ liệu xác nhận cho từng profile.** Với snapshot đầy đủ, file thay thế tập bản ghi của đúng entity và phạm vi đã đăng ký. Với cập nhật một phần, tạo phiên bản mới từ dữ liệu nền và áp dụng thay đổi theo khóa. Việc thiếu một khóa trong file cập nhật không mang nghĩa xóa. Đợt đầu nên chọn một profile và một chế độ đã thống nhất. File rỗng cần chính sách rõ trước khi được phép thay thế toàn bộ.

**Tương thích lịch sử.** Migration hiện yêu cầu `silver_snapshot.manifest_ref` khác null và reader hiện giải mã manifest/Parquet. Cần phân biệt snapshot cũ và SQL mới bằng loại lưu trữ, điều chỉnh constraint tương ứng và định tuyến reader, hoặc backfill có đối soát. Không điền đường dẫn manifest giả. Khi chuyển một scope đã có dữ liệu sang SQL, materialize cả các entity kế thừa cần xuất hiện trong view rồi so sánh số dòng và khóa trước khi đổi head.

## 6. Retry, chạy đồng thời và lỗi

| Tình huống | Hành vi cần triển khai |
|---|---|
| Giao lại cùng yêu cầu | Unique key gồm scope, dataset, Bronze object/version và phiên bản cấu hình xử lý. Job đã publish trả snapshot cũ. Cấu hình thay đổi tạo lần xử lý mới có lineage riêng |
| Worker chết sau khi claim | Job hết lease được đưa về hàng chờ nếu còn lượt thử. Token cũ mất quyền publish. Staging của lần cũ được dọn theo chính sách lưu giữ |
| MinIO/DB tạm gián đoạn | Retry có backoff và giới hạn số lần. Khi hết lượt chuyển trạng thái thất bại kèm nguyên nhân |
| Checksum sai hoặc dữ liệu vi phạm rule | Chặn publish, lưu mã lỗi. Dữ liệu nguồn sửa tạo Bronze version mới. Sửa mapping tạo phiên bản profile mới |
| Hai worker cùng cập nhật một scope | So sánh base snapshot khi khóa head. Nếu head đã đổi, tính lại trên nền mới khi thứ tự nguồn cho phép. File cũ đến muộn cần được xử lý theo thứ tự nghiệp vụ của profile |
| Commit thành công nhưng worker mất kết nối | Đọc lại job theo khóa yêu cầu. Nếu đã `PUBLISHED` thì dùng snapshot đó |

Theo dõi số job chờ, tuổi job lâu nhất, số lần hết lease, thời gian clean/publish, tỷ lệ bị chặn theo nguồn và mã lỗi. Log mang job ID và locator, hạn chế ghi giá trị ô chứa dữ liệu cá nhân. Tài khoản worker chỉ cần đọc đúng vùng Bronze và ghi các bảng xử lý Silver được cấp quyền.

## 7. Trình tự triển khai và nghiệm thu

1. **Chốt profile đầu tiên.** Chọn entity, scope, khóa nghiệp vụ, chế độ nạp, mapping, ngưỡng lỗi và giới hạn file. Chuẩn bị một XLSX và một JSON biểu diễn cùng hai bản ghi.
2. **Hoàn thiện đường ghi SQL trước.** Tạo migration bổ sung, bảng version, lineage, publisher và view. Chạy có kiểm soát từ một Bronze XLSX đã tồn tại. Đối chiếu giá trị, kiểu, khóa, số dòng và locator.
3. **Bổ sung JSON xuyên suốt.** Kiểm tra cấu hình datasource, factory, validation Bronze, capture file gốc và adapter cleaning. Chạy JSON tương đương qua cùng profile đích. Hai file cho cùng kết quả nghiệp vụ, mỗi kết quả truy về đúng nguồn riêng.
4. **Bật tự động cho một nguồn.** Thêm enqueue vào transaction hoàn tất ingestion, service worker, lease/recovery, API trạng thái và retry. Thử tắt worker trước khi ingest rồi bật lại. Job phải còn trong DB và tự hoàn tất mà người dùng không gọi cleaning thủ công.
5. **Kiểm thử tình huống mất dữ liệu.** Dừng worker giữa hai batch, gửi lại cùng yêu cầu, chạy hai job cùng scope, đưa file có dòng sai và file cập nhật thiếu khóa cũ. Kiểm tra head, số dòng, dữ liệu kế thừa và báo cáo sau mỗi ca.
6. **Chuyển luồng đọc.** Đối soát snapshot hiện hữu, phân quyền view SQL, bật từng profile. Khi cần ngừng rollout, tắt enqueue cho nguồn và giữ nguyên snapshot đã công bố để điều tra.

**Ví dụ nghiệm thu.** File Excel có header ở dòng 1, mã ngành ở dòng 2 và 3. Sau ingestion thành công, chờ Silver đạt `PUBLISHED`, truy vấn `SELECT * FROM silver.current_major` trong scope thử nghiệm và đối chiếu hai khóa. Lặp lại bằng JSON trong scope thử nghiệm độc lập. Tiếp theo gửi một file có khóa bắt buộc rỗng. Bronze có thể thành công nếu policy Bronze cho phép, Silver phải ghi lỗi đúng locator và view vẫn trả dữ liệu hợp lệ trước đó.

## 8. Đối chiếu mã nguồn

- [Claim, lease và transaction kết thúc ingestion](../../app/repositories/ingestion_repo.py)
- [Điểm gọi finish sau khi thực thi ingestion](../../app/modules/ingestion/execution.py)
- [Đăng ký source reader](../../app/modules/ingestion/readers/factory.py) và [Excel reader](../../app/modules/ingestion/readers/excel_reader.py)
- [Runner cleaning giới hạn theo demo](../../app/modules/pipeline/manual_cleaning.py) và [API thủ công](../../app/modules/pipeline/router.py)
- [Publisher và reader Parquet hiện tại](../../app/modules/pipeline/silver_publication.py) và [migration run, snapshot, head](../../infra/postgres/migrations/20261015_p5_silver.sql)
- [Các entity và khóa theo domain](silver-schemas/manifest.json), [dịch vụ Compose](../../docker-compose.yml) và [dependencies của worker](../../requirements-worker.txt)
