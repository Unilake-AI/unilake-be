-- Cấu hình tự chạy chỉ có hiệu lực khi quản trị bật rõ ràng cho từng bộ dữ liệu.
BEGIN;
CREATE TABLE IF NOT EXISTS app.auto_pipeline_policy (
    dataset_id BIGINT PRIMARY KEY REFERENCES app.dataset(dataset_id),
    revision INTEGER NOT NULL CHECK (revision > 0),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    definition JSONB NOT NULL,
    updated_by VARCHAR(100) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app.auto_pipeline_job (
    job_id VARCHAR(36) PRIMARY KEY DEFAULT gen_random_uuid()::text,
    ingestion_job_id BIGINT NOT NULL UNIQUE REFERENCES app.ingestion_job(ingestion_job_id),
    dataset_id BIGINT NOT NULL REFERENCES app.dataset(dataset_id),
    policy JSONB NOT NULL,
    bronze_input JSONB NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'QUEUED'
        CHECK (status IN ('QUEUED','RUNNING','RETRY_WAIT','SUCCESS','QUARANTINED','FAILED','CANCELLED')),
    attempt_number INTEGER NOT NULL DEFAULT 0,
    lease_token VARCHAR(36),
    lease_expires_at TIMESTAMPTZ,
    worker_id VARCHAR(100),
    next_attempt_at TIMESTAMPTZ,
    error_code VARCHAR(100),
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
-- Một bộ dữ liệu chỉ có một lượt tự chạy đang giữ quyền xử lý.
CREATE UNIQUE INDEX IF NOT EXISTS ux_auto_pipeline_dataset_running
    ON app.auto_pipeline_job(dataset_id) WHERE status = 'RUNNING';
CREATE INDEX IF NOT EXISTS ix_auto_pipeline_queue
    ON app.auto_pipeline_job(status, next_attempt_at, created_at);
CREATE TABLE IF NOT EXISTS app.auto_pipeline_attempt (
    job_id VARCHAR(36) NOT NULL REFERENCES app.auto_pipeline_job(job_id),
    attempt_number INTEGER NOT NULL,
    lease_token VARCHAR(36) NOT NULL,
    worker_id VARCHAR(100) NOT NULL,
    status VARCHAR(30) NOT NULL,
    error_code VARCHAR(100),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    PRIMARY KEY(job_id, attempt_number)
);
CREATE TABLE IF NOT EXISTS app.silver_worker_heartbeat (
    worker_id VARCHAR(100) PRIMARY KEY,
    current_job_id VARCHAR(36),
    last_heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE app.cleaning_run ADD COLUMN IF NOT EXISTS auto_job_id VARCHAR(36);
CREATE INDEX IF NOT EXISTS ix_cleaning_auto_job ON app.cleaning_run(auto_job_id);

CREATE OR REPLACE FUNCTION app.enqueue_silver_after_ingestion()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE selected_policy app.auto_pipeline_policy%ROWTYPE;
BEGIN
    IF NEW.status <> 'SUCCESS' OR (TG_OP = 'UPDATE' AND OLD.status = 'SUCCESS') THEN
        RETURN NEW;
    END IF;
    SELECT * INTO selected_policy FROM app.auto_pipeline_policy
        WHERE dataset_id = NEW.dataset_id AND enabled FOR SHARE;
    IF NOT FOUND THEN RETURN NEW; END IF;
    -- Không thể báo Bronze thành công mà quên tạo việc Silver khi đã bật tự chạy.
    INSERT INTO app.auto_pipeline_job(ingestion_job_id, dataset_id, policy, bronze_input)
    VALUES (NEW.ingestion_job_id, NEW.dataset_id,
        selected_policy.definition || jsonb_build_object(
            'revision', selected_policy.revision, 'requested_by', selected_policy.updated_by),
        NEW.output)
    ON CONFLICT (ingestion_job_id) DO NOTHING;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS enqueue_auto_silver ON app.ingestion_job;
CREATE TRIGGER enqueue_auto_silver AFTER INSERT OR UPDATE OF status ON app.ingestion_job
    FOR EACH ROW EXECUTE FUNCTION app.enqueue_silver_after_ingestion();
COMMIT;
