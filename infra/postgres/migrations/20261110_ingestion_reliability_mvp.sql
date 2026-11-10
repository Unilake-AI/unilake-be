-- Reliability MVP: giữ lịch sử chạy lại trong cùng bảng job và theo dõi worker độc lập.
BEGIN;

ALTER TABLE app.ingestion_job
    ADD COLUMN IF NOT EXISTS root_job_id BIGINT REFERENCES app.ingestion_job(ingestion_job_id),
    ADD COLUMN IF NOT EXISTS attempt_number INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS bronze_output JSONB,
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS retryable BOOLEAN NOT NULL DEFAULT FALSE;

-- Job đã có trước migration là lần đầu của chính chuỗi đó.
UPDATE app.ingestion_job
SET root_job_id = ingestion_job_id
WHERE root_job_id IS NULL;

CREATE INDEX IF NOT EXISTS ix_ingestion_job_root_attempt
    ON app.ingestion_job(root_job_id, attempt_number);

CREATE TABLE IF NOT EXISTS app.ingestion_worker_heartbeat (
    worker_id VARCHAR(100) PRIMARY KEY,
    current_job_id BIGINT REFERENCES app.ingestion_job(ingestion_job_id),
    last_heartbeat_at TIMESTAMP NOT NULL
);

COMMIT;
