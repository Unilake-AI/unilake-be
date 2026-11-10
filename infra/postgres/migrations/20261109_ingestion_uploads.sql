-- Bổ sung tiếp nhận file; không thay đổi hoặc xóa các job đang có.
BEGIN;
ALTER TABLE app.ingestion_job ADD COLUMN IF NOT EXISTS upload_input JSONB;
CREATE INDEX IF NOT EXISTS ix_ingestion_upload_queue
    ON app.ingestion_job(status, ingestion_job_id) WHERE job_type = 'UPLOAD_V1';
COMMIT;
