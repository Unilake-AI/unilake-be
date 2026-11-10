-- Chạy một lần trước khi dùng API catalog mới (có thể chạy lại an toàn).
-- Không sửa schema dữ liệu nghiệp vụ hay các bảng catalog_table/catalog_column.
BEGIN;
CREATE TABLE IF NOT EXISTS app.catalog_metadata (
    domain_id VARCHAR(50) NOT NULL,
    dataset VARCHAR(150) NOT NULL,
    metadata_payload JSONB NOT NULL,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (domain_id, dataset)
);
COMMIT;
