# Silver mặc định tự động

Sau khi nạp Bronze thành công, hệ thống tự xếp hàng và xử lý Silver. Không cần chọn dataset, chọn profile hoặc bật tự chạy trước mỗi file. Trang `/data/auto-pipeline` là màn hình giám sát toàn hệ thống; bộ lọc trên trang chỉ dùng tìm lượt chạy.

## Luồng xử lý

1. STAFF hoặc quản trị tải file vào dataset mới/có sẵn bằng API Ingestion hiện tại. Backend vẫn kiểm tra quyền lĩnh vực và đích nhận.
2. Ingestion Worker lưu Bronze, đối chiếu nội dung và phiên bản object.
3. Trong cùng giao dịch Ingestion `SUCCESS`, trigger gọi `app.enqueue_default_silver`. Một lần nạp chỉ có một job Silver; rollback Bronze cũng rollback job.
4. Silver Worker đọc đúng bản Bronze đã chốt. Nếu có override đang bật và khớp đúng file/nguồn, dùng override; nếu không, dùng quy tắc hệ thống `auto-source-v1`.
5. Quy tắc hệ thống nhận diện nghiệp vụ khi khớp duy nhất toàn bộ cấu trúc có sẵn. File chưa khớp mẫu được xử lý theo cột thực tế, giữ mã nguồn và không tự gán ý nghĩa nghiệp vụ.
6. Làm sạch, chuẩn hóa, kiểm tra khóa/cấu trúc và lưu hồ sơ cách ly. File có dòng không đạt không được tự thay thế bản Silver tốt trước đó.
7. File đạt được công bố thành Silver, ghi nguồn gốc và kết quả job trong cùng giao dịch. Mỗi file có scope ổn định riêng theo dataset/nguồn/tên file; hai file không ghi đè nhau. Nạp lại file thay toàn bộ bản file hiện hành đã kiểm tra, kể cả khi cấu trúc đổi; snapshot cũ vẫn giữ trong lịch sử.
8. Việc đổi Silver head tự tạo việc Gold theo luồng [Silver → Gold](gold-pipeline.md).

Quản trị không cần mở màn hình này thì pipeline vẫn chạy. API/FE không trực tiếp xử lý file trong request tải lên; worker chạy độc lập từ hàng đợi PostgreSQL, không dùng Redis.

## Quy tắc mặc định

- Dùng đúng quy định cấu trúc đã đóng gói, không đoán loại dữ liệu từ tên file.
- Giữ mã như `007` và `7` tách biệt; không tự hợp nhất mã giữa các nguồn hoặc tự tạo crosswalk.
- Tên cột được chuẩn hóa và vẫn lưu liên hệ với cột nguồn. Các cột thông tin cá nhân đã quy định loại khỏi Silver tiếp tục bị loại.
- Khi không khớp mẫu nghiệp vụ, các chuỗi CSV được giữ dạng chuỗi; không đoán kiểu số/ngày từ vài giá trị. Kiểu native rõ ràng trong Excel được giữ theo cấu trúc nguồn. Cột mã luôn được giữ dạng chuỗi.
- Ngày tháng chỉ dùng định dạng xác định rõ; trường hợp mơ hồ không được tự đổi ngày/tháng.
- Không tự xóa bản ghi trùng để che lỗi, không tự điền dữ liệu còn thiếu, không tự công bố phần file lỗi.
- Gold của cấu trúc chưa nhận diện chỉ có số bản ghi theo nguồn; không coi đó là KPI nghiệp vụ đã hợp nhất.

## Chính sách riêng và production

Cấu hình theo dataset còn được hỗ trợ qua API để tương thích hoặc xử lý ngoại lệ. Đây là override, không phải điều kiện để pipeline mặc định chạy:

- `enabled=false` là lệnh tạm dừng rõ ràng và được giữ nguyên.
- Profile riêng chỉ áp dụng khi đúng dataset/nguồn/tên file; file khác cùng dataset vẫn được xử lý mặc định.
- Cấu hình riêng có revision và fingerprint. Job đã nhận giữ nguyên quy tắc/phiên bản Bronze; thay/tắt cấu hình hủy việc riêng còn chờ theo quy định cũ. Việc mặc định còn chờ bị hủy khi dataset được tạm dừng.

Production không tự phê duyệt profile tùy ý. Quy tắc hệ thống có bằng chứng `automatic_recipe` trong manifest, được kiểm tra với job đã chốt, fingerprint profile, scope, schema và toàn bộ lineage Bronze; kiểm tra lease nằm trong giao dịch công bố. Không chấp nhận bằng chứng giả, nguồn khác hoặc công bố một phần qua nhãn automatic. Profile thủ công vẫn cần đầy đủ phê duyệt chủ lĩnh vực như trước.

## Giám sát và API

Tiền tố `/api/v1`, Bearer token hiện có:

| API | Nội dung |
| --- | --- |
| `GET /pipeline/auto/overview` | Trạng thái đã triển khai mặc định, thống kê job, số dataset tạm dừng, số worker còn hoạt động |
| `GET /pipeline/auto/jobs` | Mọi lượt chạy; lọc tùy chọn dataset/lần nạp, phân trang; có tên dataset và file |
| `GET /pipeline/auto/jobs/{job_id}` | Kết quả Silver, hồ sơ lỗi và số bản ghi |
| `GET /pipeline/auto/jobs/{job_id}/attempts` | Lịch sử các lần thử |
| `POST /pipeline/auto/jobs/{job_id}/retry` | Quản trị thử lại lượt lỗi; giữ Bronze và quy tắc đã chốt |
| `GET /pipeline/auto/workers` | Heartbeat và trạng thái còn hoạt động trong 45 giây gần nhất |
| `GET/PUT /pipeline/auto/datasets/{dataset_id}` | Override cũ, không bắt buộc cho hoạt động mặc định |

Trang giám sát không còn form chọn dataset/profile và lưu bật tự chạy. Mặc định hiển thị toàn bộ hàng đợi/kết quả, tự cập nhật mỗi 5 giây. Nếu chưa cập nhật DB hoặc không có worker hoạt động, UI báo rõ, không suy ra hệ thống đang chạy chỉ từ heartbeat cũ. STAFF tiếp tục xem trạng thái Silver ngay trong chi tiết lần nạp, không được đổi quy tắc hoặc retry.

## Cập nhật database đang có

```bash
make db-auto-upgrade
make db-silver-default-upgrade
docker compose up -d --build silver-worker gold-worker
```

Với rollout đang có worker cũ, dựng image mới trước, dừng Silver Worker cũ, áp dụng migration rồi khởi động lại để worker cũ không nhận job theo định dạng mới.

Migration `20261116_silver_default_automation.sql` cũng tiếp nhận bản Bronze thành công mới nhất của mỗi dataset/nguồn/file chưa có Silver hoặc job. Không chạy lại tất cả phiên bản cũ, không ghi đè job hiện có, không tự bật dataset đã tạm dừng. Chạy migration lại không tạo trùng. Database mới tự chạy cùng migration qua `07-silver-default-automation.sh`.

Silver Worker cần PostgreSQL, quyền đọc Bronze và ghi Silver. Gold Worker cần quyền đọc Silver/ghi Gold. Docker image đã đóng gói các cấu trúc nguồn/Silver dùng cho quy tắc mặc định.

## Kiểm chứng

```bash
AUTO_PIPELINE_LIVE_TEST=1 .venv/bin/pytest tests/test_default_silver_live.py tests/test_auto_pipeline_live.py --no-cov
```

Kiểm thử thật dùng database và object riêng: không policy/crosswalk, production, Bronze → Silver → Gold, giữ mã có số 0 đầu, loại cột PII, nạp lại/đổi schema, nhiều file một dataset, cách ly file lỗi giữ bản tốt, backfill/idempotency/rollback. Bằng chứng automatic giả bị từ chối; kiểm thử quyền/phê duyệt thủ công cũ vẫn được giữ.
