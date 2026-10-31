# Review Bronze → Silver — 16/10/2026

Đối chiếu commit Bronze 9aad8da, API/worker/repository/capture, handoff và S2–P5.
Không phải review toàn bộ frontend/auth.

## Findings

1. **Chưa tự nối Bronze → Silver.** execution.py kết thúc ở repo.finish(output);
   pipeline/router.py chưa có endpoint. Cần normalization job/consumer riêng để tự dispatch,
   quản lý trạng thái, retry và version. SUCCESS ingestion chỉ xác nhận Source → Bronze.
2. **SQL → Bronze chưa đi tiếp được vào Silver mới.** BronzeCapture xuất snapshot.parquet,
   nhưng read_file_snapshot/read_data_source_batch chỉ nhận CSV/EXCEL. Còn thiếu adapter
   cho typed DB snapshot; không lấy việc ingest SQL thành công làm bằng chứng end-to-end.
3. **Job chưa đóng băng cấu hình catalog.** Job lưu ID/source_table, worker truy vấn config
   hiện tại khi thực thi; output giữ uri/version/checksum/size nhưng thiếu parser config/version.
   Thay config sau enqueue có thể đổi nguồn được đọc; thay sau SUCCESS làm thiếu bằng chứng
   replay. Cần snapshot config không chứa bí mật hoặc fingerprint/version immutable gắn với job.
   Chưa sửa production trong lượt review này; demo yêu cầu giữ catalog cố định.
4. **Demo cũ không nhận key worker.** CLI chỉ chấp nhận demo/, còn worker tạo domain/date/job-id.
   Đã thêm --ingestion-job cho catalog demo riêng, xác minh SUCCESS/job type/domain/key/bucket,
   version/size/SHA/rows, dùng provenance ID thật. Không đổi worker thành tự chạy Silver.
5. **Nghiệm thu production còn mở.** Rehearsal không chứng minh G1/39-rule DQ gate, toàn bộ
   22 entity/FK hay concurrency live. Ba entity độc lập chỉ là smoke test có thể quan sát.

## Những phần đã có

- API kiểm tra quyền, nguồn/dataset/layer, commit QUEUED trước I/O và trả 202.
- PostgreSQL queue, claim SKIP LOCKED, khóa source và partial unique index chống job hoạt động trùng.
- Worker revalidate, heartbeat/lease, chặn publication sau khi hết lease.
- Copy file trước parse, lưu bytes gốc; không dựng lại CSV/Excel từ rows.
- Bronze bắt buộc version, đọc lại đúng version và so SHA-256.
- Job SUCCESS là authority; object mồ côi không đồng nghĩa publication.
- S2/S3/S4 phân tách mapping, kiểu, ID/dictionary và có locator/version.
- P5 validate snapshot, Parquet readback, manifest và transaction/CAS pointer.
- Silver lỗi không xóa Bronze hoặc đổi status ingest đã thành công.

## Cách test đã chỉnh

Catalog demo → API → worker → Bronze SUCCESS → CLI theo job ID → Silver rehearsal.
File sai ngày/mã chưa biết phải Bronze SUCCESS nhưng Silver FAILED, head không đổi.
File không tồn tại phải Bronze FAILED và không được chuyển Silver.
Reports lưu ingestion_job_id và receipt.snapshot_id để đối chiếu.
Upload trực tiếp vẫn hữu ích cho storage riêng nhưng không test API/worker/counter/lease.

## Bằng chứng và giới hạn

- Đã đọc execution.py, ingestion_repo.py, bronze_capture.py, ingestion_worker.py,
  ingestion/router.py, file_snapshot.py và các module pipeline/Silver.
- 89 tests đạt: live_demo_files, bronze_storage, ingestion_execution, silver_publication.
  Test async chạy ngoài sandbox Windows do socket nội bộ bị treo trong sandbox.
- Docker: PostgreSQL/MinIO healthy, init exit 0, worker Up khi kiểm tra.
- Chưa apply seed/migration hoặc gửi job thật trong lượt này; bridge mới chưa chạy live E2E.

[Hướng dẫn thực hành](manual-live-demo.md).
