# API VIEWER

Các đường dẫn dưới đây nằm dưới `/api/v1/viewer`. Swagger tại `/docs` mô tả schema response và xác thực Bearer. Mỗi request kiểm tra phiên đăng nhập và role `VIEWER` hiện hành. Phạm vi đọc gồm các dataset `ACTIVE` của ba domain `hr`, `academic` và `admissions` trên toàn trường, kể cả dataset chưa gán phòng sở hữu.

| GET | Kết quả | Bộ lọc |
| --- | --- | --- |
| `/context` | Tài khoản, role và ba domain được cấp quyền | |
| `/overview` | Số dataset, số nguồn, số domain `READY` và trạng thái từng domain | |
| `/domains/{domain}/overview` | Trạng thái công bố, thời điểm công bố gần nhất và chỉ số nổi bật theo domain | |
| `/domains/{domain}/analytics` | Thống kê Gold hiện hành theo dataset, nguồn và thực thể | `dataset_id`, `entity`, `metric`, `dimension_value` |
| `/datasets` | Danh sách metadata dataset | `domain`, `dataset_id` |
| `/datasets/{dataset_id}` | Metadata dataset, bảng và cột | |
| `/quality/overview` | Ba nhóm `quality`, `publication` và `freshness` | |
| `/quality/issues` | Danh sách yêu cầu xử lý gồm mã yêu cầu, dataset, phòng, domain, trạng thái và mốc thời gian | `domain`, `status`, `dataset_id` |

Ba API danh sách trả `items`, `total`, `limit` và `offset`. Mặc định `limit=20` và `offset=0`, giới hạn mỗi trang là 100. `total` áp dụng các bộ lọc trước khi phân trang, kể cả khi trang rỗng. Chi tiết dataset ngoài phạm vi hoặc chưa tồn tại trả `404 DATASET_NOT_FOUND`.

| Trạng thái | Điều kiện |
| --- | --- |
| `NO_DATA` | Domain chưa có dataset hoạt động. |
| `PENDING` | Domain có dataset hoạt động và chưa có nguồn Gold hiện hành. |
| `PARTIAL` | Domain đã có nguồn Gold hiện hành và còn nguồn chưa hiện hành hoặc dataset chưa có phạm vi xử lý Gold. |
| `READY` | Mọi nguồn đều có Gold hiện hành và mọi dataset hoạt động đều có phạm vi xử lý Gold. |

`highlight_metric` chỉ có giá trị khi domain `READY` và có đúng một chỉ số `record_count`. Giá trị đi cùng `dataset_id`, `scope`, `entity` và đơn vị `records`. FE có thể hiển thị `message` với `PENDING`, `NO_DATA` và `PARTIAL`. Analytics mặc định dùng `metric=record_count`. Dùng `dimension_value` với metric dạng `record_count_by_*`.

`quality` đếm các yêu cầu theo `ASSIGNED`, `PENDING_VERIFICATION` và `VERIFIED`. `publication` đếm nguồn theo phạm vi xử lý Gold và đưa riêng số dataset chưa có phạm vi này qua `datasets_without_scope`. `gold_job_failed` đếm nguồn có lượt tạo Gold mới nhất thất bại. `sync_failed` đếm nguồn có Gold hiện hành nhưng tác vụ đồng bộ bảng nghiệp vụ gặp lỗi hoặc bị chặn. `freshness` trả thời điểm công bố Gold hiện hành gần nhất cho mỗi domain, hoặc `null`.
