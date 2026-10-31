-- Migration bổ sung; áp dụng cho volume hiện có trước khi triển khai API/worker.
BEGIN;
ALTER TABLE app.ingestion_job
    ADD COLUMN IF NOT EXISTS source_table VARCHAR(200),
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS requested_by VARCHAR(100),
    ADD COLUMN IF NOT EXISTS stage VARCHAR(30),
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS output JSONB;
CREATE UNIQUE INDEX IF NOT EXISTS uq_ingestion_execution_active_source
    ON app.ingestion_job(data_source_id)
    WHERE job_type = 'EXECUTION_V1' AND status IN ('QUEUED', 'RUNNING');
CREATE INDEX IF NOT EXISTS ix_ingestion_execution_queue
    ON app.ingestion_job(status, ingestion_job_id) WHERE job_type = 'EXECUTION_V1';
COMMIT;
