BEGIN;
SET LOCAL search_path TO app, public;

-- Lưu thông tin đọc lại Bronze một lần cho cả lượt, không sao chép giá trị gốc.
ALTER TABLE cleaning_run ADD COLUMN IF NOT EXISTS quarantine_source JSON;

CREATE TABLE IF NOT EXISTS quarantine_record (
    record_id VARCHAR(36) PRIMARY KEY,
    cleaning_run_id VARCHAR(36) NOT NULL REFERENCES cleaning_run(run_id),
    locator TEXT NOT NULL,
    record_position BIGINT NOT NULL CHECK (record_position > 0),
    issues JSON NOT NULL,
    error_codes JSONB NOT NULL,
    UNIQUE (cleaning_run_id, locator)
);

CREATE INDEX IF NOT EXISTS ix_quarantine_record_error_codes
    ON quarantine_record USING GIN (error_codes jsonb_path_ops);
COMMIT;
