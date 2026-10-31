-- Chạy trước khi khởi động backend/worker mới; không xóa volume hoặc dữ liệu cũ.
BEGIN;
ALTER TABLE app.ingestion_job
    ADD COLUMN IF NOT EXISTS validation_input JSONB,
    ADD COLUMN IF NOT EXISTS validation_report JSONB;
COMMIT;
