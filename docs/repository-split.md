# Bàn giao tách backend — 31/10/2026

## Kết quả

Bản source độc lập nằm trong folder `unilake-backend/` của workspace ban đầu.
Có thể di chuyển nguyên folder sang vị trí khác. Chưa khởi tạo Git, chưa tạo
remote và chưa push. Code gốc và frontend không bị sửa.

## Thay đổi phục vụ việc tách

- Đưa nội dung `backend/` ra gốc repo mới; giữ API và worker trong cùng repo.
- Mang theo docs/schema, profile, fixture synthetic, script và hạ tầng PostgreSQL.
- Chỉnh đường dẫn repo root trong test, manual cleaning và script demo/preview.
- Settings chỉ đọc env ở repo hiện tại; bỏ fallback tới `.env` của repo cha.
- CLI Bronze local mặc định dùng `data/bronze` trong repo hiện tại.
- Compose build từ gốc repo, bỏ FE; env file service account là tùy chọn lúc
  bootstrap, nhưng thao tác storage vẫn yêu cầu provision account hợp lệ.
- Docker image kèm docs và data/standardization cho cleaning runtime.
- Docker requirements bổ sung PyJWT và pwdlib mà auth runtime đang sử dụng.
- Giới hạn setuptools package discovery vào `app*`.
- CI, Makefile, Pyright, README và ignore rules được chỉnh cho repo độc lập.
- Không copy secret env, Git history, cache, virtualenv hoặc node_modules.

Schema/profile còn nằm dưới docs/data để giữ contract hiện có. Không đổi cấu trúc
schema và không đổi cơ chế phê duyệt production trong lượt tách repo.

## Kiểm chứng

- Parse AST toàn bộ Python source: đạt.
- Schema runtime của ba domain có đủ và JSON parse được.
- `docker compose config --quiet`: đạt, không cần file env secret ở repo gốc.
- Ruff trên ba module runtime đã đổi và scripts/standardization: đạt.
- Copy source ra thư mục tạm ngoài monorepo rồi chạy pytest; dùng Python/dependency
  từ virtualenv hiện có nhưng PYTHONPATH trỏ vào bản copy độc lập.
- Test dùng `-o addopts='' --continue-on-collection-errors -q --tb=short`:
  **356 passed, 85 failed, 2 skipped, 25 collection errors**.
- Chạy cùng lệnh trên code gốc cho đúng các số trên; tập tên test fail/error
  giống nhau, không phát sinh test fail/error mới do tách.

Đây không phải xác nhận test suite xanh hoặc đạt coverage: lượt so sánh tắt
coverage mặc định để đối chiếu failures. Các lỗi cũ gồm schema/sample/contract,
ranh giới module, và collection thiếu psycopg trong virtualenv hiện tại. Cần cài
đầy đủ dependency trong môi trường sạch rồi chạy lại trước nghiệm thu CI.

Chưa build Docker image, chưa chạy API/worker với PostgreSQL/MinIO thật và chưa
nghiệm thu pipeline end-to-end. CI đã chuyển đường dẫn nhưng các lỗi code/test
kế thừa còn có thể làm CI thất bại.

## Tài nguyên và phiên bản

Repo có fixture/sample của dự án, không copy data runtime của máy local.
Schema/profile ngoài app được hỗ trợ qua source checkout và Docker image;
chưa cam kết phân phối riêng bằng Python wheel.

`docs/` kế thừa một số hướng dẫn đường dẫn monorepo. Dùng README ở gốc repo mới
làm hướng dẫn khởi chạy hiện hành. OpenAPI có sẵn tại docs/api/openapi.json để FE
có thể lấy contract; chưa cấu hình cơ chế phát hành contract tự động.
