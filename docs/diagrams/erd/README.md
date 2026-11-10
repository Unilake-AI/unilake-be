# UniLake AI – Bộ ảnh ERD Database

Bộ ảnh ERD được xuất từ Archify (`showcase` profile, độ phân giải `2048x1320`) phản ánh cấu trúc schema `app` trong [02-schema.sql](../../../infra/postgres/init/02-schema.sql), các migration bổ sung trong `infra/postgres/migrations/` và các mô hình SQLAlchemy trong `app/modules/*/models.py`.

## Danh sách ảnh hiện có

| STT | Phạm vi | Ảnh PNG |
|---|---|---|
| 00 | Tổng quan 9 cụm phân hệ & liên kết chéo (66 bảng) | [unilake-erd-overview.visual-check.2048x1320.light.png](./unilake-erd-overview.visual-check.2048x1320.light.png) |
| 02 | Chương trình Đào tạo, Lớp học & Điểm (7 bảng) | [unilake-erd-02-curriculum-class.visual-check.2048x1320.light.png](./unilake-erd-02-curriculum-class.visual-check.2048x1320.light.png) |
| 03 | Nhân sự & Giảng viên (7 bảng) | [unilake-erd-03-hr-lecturer.visual-check.2048x1320.light.png](./unilake-erd-03-hr-lecturer.visual-check.2048x1320.light.png) |
| 04 | Bảo mật & Phân quyền RBAC (6 bảng) | [unilake-erd-04-security-rbac.visual-check.2048x1320.light.png](./unilake-erd-04-security-rbac.visual-check.2048x1320.light.png) |
| 05 | Data Lake, Ingestion & Cleaning (10 bảng) | [unilake-erd-05-ingestion-lake.visual-check.2048x1320.light.png](./unilake-erd-05-ingestion-lake.visual-check.2048x1320.light.png) |
| 06 | Silver Management & Identity Crosswalk (8 bảng) | [unilake-erd-06-silver-crosswalk.visual-check.2048x1320.light.png](./unilake-erd-06-silver-crosswalk.visual-check.2048x1320.light.png) |
| 07 | Catalog, Lineage & Data Quality Rules (7 bảng) | [unilake-erd-07-catalog-lineage-dq.visual-check.2048x1320.light.png](./unilake-erd-07-catalog-lineage-dq.visual-check.2048x1320.light.png) |
| 08 | Quản lý Sự cố Chất lượng Dữ liệu (3 bảng + 6 tham chiếu) | [unilake-erd-08-dq-issues.visual-check.2048x1320.light.png](./unilake-erd-08-dq-issues.visual-check.2048x1320.light.png) |
| 09 | AI Analytics & Text-to-SQL (6 bảng) | [unilake-erd-09-ai-nlq.visual-check.2048x1320.light.png](./unilake-erd-09-ai-nlq.visual-check.2048x1320.light.png) |
| 10 | Dashboard, Biểu đồ & KPI (4 bảng) | [unilake-erd-10-dashboard-kpi.visual-check.2048x1320.light.png](./unilake-erd-10-dashboard-kpi.visual-check.2048x1320.light.png) |
