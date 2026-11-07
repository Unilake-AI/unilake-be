BEGIN;
SET LOCAL search_path TO app, public;

-- Mỗi lần chạy cleaning có ID riêng, kể cả khi dùng lại cùng job Bronze.
CREATE TABLE IF NOT EXISTS cleaning_run (
    run_id VARCHAR(36) PRIMARY KEY,
    ingestion_job_id BIGINT NOT NULL REFERENCES ingestion_job(ingestion_job_id),
    dataset_id BIGINT NOT NULL REFERENCES dataset(dataset_id),
    domain VARCHAR(50) NOT NULL,
    entity VARCHAR(100) NOT NULL,
    requested_by VARCHAR(100) NOT NULL,
    session VARCHAR(40) NOT NULL,
    bronze_version TEXT NOT NULL,
    input_version TEXT,
    cleaning_status VARCHAR(20) NOT NULL,
    publication_status VARCHAR(30) NOT NULL,
    records_read BIGINT,
    records_passed BIGINT,
    records_quarantined BIGINT,
    records_collapsed BIGINT,
    records_published BIGINT NOT NULL,
    silver_snapshot_id VARCHAR(36),
    report_ref JSON,
    error_code VARCHAR(80),
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_cleaning_run_started
    ON cleaning_run(started_at DESC, run_id DESC);
CREATE INDEX IF NOT EXISTS ix_cleaning_run_dataset_started
    ON cleaning_run(dataset_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_cleaning_run_job_started
    ON cleaning_run(ingestion_job_id, started_at DESC);
COMMIT;
