-- Ghi rõ yêu cầu được giao theo phòng ban hay được duyệt trả về người nạp file.
BEGIN;
ALTER TABLE app.data_quality_issue
    ADD COLUMN IF NOT EXISTS assignment_mode VARCHAR(30) NOT NULL DEFAULT 'DEPARTMENT';
ALTER TABLE app.data_quality_issue
    ADD COLUMN IF NOT EXISTS origin_upload_job_id BIGINT REFERENCES app.ingestion_job(ingestion_job_id);
COMMIT;
