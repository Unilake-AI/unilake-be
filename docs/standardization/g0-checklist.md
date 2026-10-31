# G0 — Nghiệm thu và phụ thuộc

Cập nhật 15/10/2026: phần file handoff B08 đã có provenance/version/locator và test;
openpyxl đã dùng được trong .venv. Xem [G1 resolution](g1-resolution.md). B08 còn
DB snapshot/engine dispatch nếu dùng RDBMS; G0 vẫn chưa đóng và các kết quả môi
trường baseline bên dưới không phải bằng chứng live mới.

Ngày baseline: 14/10/2026. **G0 = NOT PASSED**. Không có người xác nhận nghiệp vụ được suy diễn từ việc yêu cầu triển khai P0.

| Hạng mục | Kết quả | Bằng chứng / việc còn lại |
|---|---|---|
| P0.1 Baseline | DONE (artifact) | baseline.md, evidence/schema-inventory.json, scope-matrix.md |
| P0.2 Unified Schema và mapping | PARTIAL | Pin nguồn và trích toàn bộ FK; chưa có approval/crosswalk nguồn thật |
| P0.3 Môi trường | PARTIAL | Compose hợp lệ; Dockerfile đúng; bổ sung API_BASE_URL. Docker Engine chưa chạy nên DB/MinIO chưa xác minh live |
| P0.4 Config/profile | DONE (design) | source-profile-contract.md; chưa thay contract runtime của S2 |
| P0.5 Fixtures | DONE (starter set) | Ba domain, expected outputs và ca lỗi ở data/standardization/p0; chưa phủ 22 entity như S1/P6 |

## Danh sách phụ thuộc

Owner dưới đây là vai trò cần phân công; tên thực tế chưa được cung cấp. Hạn tương đối tính theo roadmap để không tự đặt deadline lịch.

| ID | Việc cần xử lý | Owner | Hạn / chặn |
|---|---|---|---|
| B01 | Khởi động Docker Desktop/Engine, chạy dịch vụ và xác minh DB/MinIO | Người vận hành môi trường | Trước đóng G0 |
| B02 | Xác nhận Unified Schema pinned và FK; cung cấp crosswalk có căn cứ cho nguồn thật | Chủ ba domain + Data Administrator | Trước G1; chặn activate mapping thật |
| B03 | Xác nhận từng field PII, mục đích analytics, quyền và retention Bronze/quarantine | Data Administrator + chủ dữ liệu | Trước G1; chặn dữ liệu thật |
| B04 | Xác nhận thẩm quyền ADM_SYS/ACA_SYS/HR_SYS ở cấp entity/field, thời điểm hiệu lực | Chủ ba domain | Trước G1 |
| B05 | Review D05 Parquet/manifest và D06 strict snapshot | Tech lead + Data Administrator | Trước G1 |
| B06 | Mở rộng fixtures toàn bộ entity và snapshot FK; khắc phục sample legacy riêng | Người triển khai S1 | Trong S1, không ghi đè Bronze |
| B07 | Hoàn thiện môi trường reader: openpyxl (factory import trực tiếp), xác minh DuckDB postgres extension; thống nhất mypy 3.13 với runtime/Docker 3.11; chạy lại suite đầy đủ | Ingestion owner + người vận hành | Trước nghiệm thu môi trường G0 |
| B08 | Chốt RDBMS engine dispatch, namespace và ReadResult → BronzeBatch adapter có object version/checksum/physical locator; chưa cần code normalization trong reader | Ingestion owner + người triển khai S2 | Chốt contract trước G1; hoàn tất handoff trước G2 |

Review checkout 14/10/2026: schema hash không đổi; 27 reader tests độc lập pass, suite đầy đủ lỗi collection do thiếu openpyxl. Không coi sự có mặt của reader hoặc integration test là bằng chứng Bronze persistence đã chạy. Xem [post-pull-review.md](post-pull-review.md).

## Lệnh nghiệm thu môi trường sau khi Engine hoạt động

Tại repo root, thực hiện trong môi trường dev được cấu hình:

```powershell
docker compose config --quiet
docker compose up -d postgres minio minio-init
docker compose ps -a
docker compose exec postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT current_database(), version();"'
docker compose logs --tail 30 minio-init
```

Phải có health DB/MinIO, minio-init exit 0 và bucket bronze/silver/gold. Xác minh Bronze versioning bằng client S3; kiểm tra ghi/đọc object giả lập dưới prefix P0 riêng và dọn đúng object đó. Không xóa volume/reinitialize DB để vượt lỗi. Lưu log đã che credentials. Kiểm tra backend login/proxy sau rebuild frontend để chứng minh API_BASE_URL có hiệu lực.

## Điều kiện đóng G0

- [x] Có inventory và hash baseline, scope matrix, decision log, field PII matrix, profile contract, fixtures.
- [ ] Các quyết định có người xác nhận thực tế và ngày/bằng chứng; không còn khoảng trống chặn S1.
- [ ] DB/MinIO và môi trường test có bằng chứng chạy thử; kết quả test đã được phân loại đúng.
- [ ] Data Administrator chấp nhận baseline và giao owner cụ thể cho phụ thuộc.

Có thể tiếp tục soạn S1 từ baseline này; chưa được coi S1 đã được duyệt hoặc activate dữ liệu thật trước các điều kiện trên.
