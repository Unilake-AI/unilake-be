BEGIN;
SET LOCAL search_path TO app, public;

CREATE TABLE IF NOT EXISTS data_quality_issue (
    issue_id VARCHAR(36) PRIMARY KEY,
    dataset_id BIGINT NOT NULL REFERENCES dataset(dataset_id),
    department_id BIGINT NOT NULL REFERENCES department(department_id),
    domain VARCHAR(50) NOT NULL,
    title VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    status VARCHAR(30) NOT NULL CHECK (status IN ('ASSIGNED', 'PENDING_VERIFICATION', 'VERIFIED')),
    created_by BIGINT NOT NULL REFERENCES app_user(app_user_id),
    resolution_job_id BIGINT REFERENCES ingestion_job(ingestion_job_id),
    verification_run_id VARCHAR(36) REFERENCES cleaning_run(run_id),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS data_quality_issue_record (
    issue_id VARCHAR(36) NOT NULL REFERENCES data_quality_issue(issue_id),
    record_id VARCHAR(36) NOT NULL REFERENCES quarantine_record(record_id),
    PRIMARY KEY (issue_id, record_id)
);

CREATE TABLE IF NOT EXISTS data_quality_issue_event (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    issue_id VARCHAR(36) NOT NULL REFERENCES data_quality_issue(issue_id),
    actor_id BIGINT NOT NULL REFERENCES app_user(app_user_id),
    event_type VARCHAR(30) NOT NULL,
    from_status VARCHAR(30),
    to_status VARCHAR(30) NOT NULL,
    details JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_quality_issue_department_status
    ON data_quality_issue(department_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS ix_quality_issue_updated
    ON data_quality_issue(updated_at DESC, issue_id DESC);
CREATE INDEX IF NOT EXISTS ix_quality_issue_event_issue
    ON data_quality_issue_event(issue_id, event_id);
COMMIT;
