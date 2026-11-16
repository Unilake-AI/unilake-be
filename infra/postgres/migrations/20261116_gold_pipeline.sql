-- Gold chỉ nhận phiên bản Silver đã công bố; hàng đợi nằm trong PostgreSQL.
BEGIN;
CREATE SCHEMA IF NOT EXISTS gold;
CREATE TABLE IF NOT EXISTS app.gold_scope (
    scope varchar(120) PRIMARY KEY REFERENCES app.silver_head(scope),
    dataset_id bigint NOT NULL REFERENCES app.dataset(dataset_id),
    domain varchar(50) NOT NULL CHECK (domain IN ('hr','academic','admissions')),
    silver_snapshot_id varchar(36) NOT NULL REFERENCES app.silver_snapshot(snapshot_id),
    generation bigint NOT NULL DEFAULT 1,
    active_job_id varchar(36),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app.gold_job (
    job_id varchar(36) PRIMARY KEY,
    scope varchar(120) NOT NULL REFERENCES app.gold_scope(scope),
    dataset_id bigint NOT NULL REFERENCES app.dataset(dataset_id),
    domain varchar(50) NOT NULL CHECK (domain IN ('hr','academic','admissions')),
    generation bigint NOT NULL,
    silver_snapshot_id varchar(36) NOT NULL REFERENCES app.silver_snapshot(snapshot_id),
    recipe_version varchar(50) NOT NULL DEFAULT 'group-counts-v1',
    recipe_fingerprint varchar(64) NOT NULL DEFAULT '9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa',
    status varchar(20) NOT NULL DEFAULT 'QUEUED'
        CHECK (status IN ('QUEUED','RUNNING','RETRY_WAIT','SUCCEEDED','FAILED','SUPERSEDED')),
    attempt_number integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 3,
    lease_token varchar(36), lease_until timestamptz,
    worker_id varchar(200), next_attempt_at timestamptz NOT NULL DEFAULT now(),
    error_code varchar(100), manifest_ref jsonb, result jsonb,
    created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
    UNIQUE(scope,generation)
);
-- Giữ cố định nội dung quy định ngay từ lúc xếp việc, kể cả khi mã xử lý được nâng cấp.
ALTER TABLE app.gold_job ADD COLUMN IF NOT EXISTS recipe_fingerprint varchar(64)
    NOT NULL DEFAULT '9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa';
CREATE INDEX IF NOT EXISTS ix_gold_job_queue ON app.gold_job(status,next_attempt_at);
CREATE TABLE IF NOT EXISTS app.gold_attempt (
    lease_token varchar(36) PRIMARY KEY,
    job_id varchar(36) NOT NULL REFERENCES app.gold_job(job_id),
    attempt_number integer NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'RUNNING',
    worker_id varchar(200) NOT NULL,
    error_code varchar(100), started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz, UNIQUE(job_id,attempt_number)
);
CREATE TABLE IF NOT EXISTS app.gold_worker (
    worker_id varchar(200) PRIMARY KEY, heartbeat_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS gold.aggregate (
    job_id varchar(36) NOT NULL REFERENCES app.gold_job(job_id),
    entity varchar(150) NOT NULL,
    metric varchar(100) NOT NULL,
    dimensions jsonb NOT NULL,
    value bigint NOT NULL CHECK (value >= 0),
    PRIMARY KEY(job_id,entity,metric,dimensions)
);

-- Khóa bộ dữ liệu hiện hành trước khi tạo việc để tránh hai người tạo trùng.
CREATE OR REPLACE FUNCTION app.enqueue_gold(p_scope text) RETURNS varchar(36)
LANGUAGE plpgsql AS $$
DECLARE h record; c record; s record; v_job varchar(36);
BEGIN
    SELECT * INTO h FROM app.silver_head WHERE scope=p_scope FOR UPDATE;
    IF h.snapshot_id IS NULL THEN RETURN NULL; END IF;
    SELECT cr.dataset_id,cr.domain INTO c FROM app.cleaning_run cr
        WHERE cr.silver_snapshot_id=h.snapshot_id
          AND cr.publication_status IN ('PUBLISHED','PUBLISHED_WITH_QUARANTINE')
        ORDER BY cr.finished_at DESC NULLS LAST,cr.run_id LIMIT 1;
    -- Không tự tổng hợp dữ liệu thử hoặc bản Silver chưa xác định được nguồn.
    IF c IS NULL OR c.domain NOT IN ('hr','academic','admissions') THEN RETURN NULL; END IF;
    INSERT INTO app.gold_scope(scope,dataset_id,domain,silver_snapshot_id)
        VALUES(p_scope,c.dataset_id,c.domain,h.snapshot_id)
        ON CONFLICT(scope) DO UPDATE SET
            dataset_id=EXCLUDED.dataset_id,domain=EXCLUDED.domain,
            silver_snapshot_id=EXCLUDED.silver_snapshot_id,
            generation=app.gold_scope.generation+1,updated_at=now()
        WHERE app.gold_scope.silver_snapshot_id IS DISTINCT FROM EXCLUDED.silver_snapshot_id;
    SELECT * INTO s FROM app.gold_scope WHERE scope=p_scope FOR UPDATE;
    v_job=gen_random_uuid()::text;
    INSERT INTO app.gold_job(job_id,scope,dataset_id,domain,generation,silver_snapshot_id)
        VALUES(v_job,p_scope,s.dataset_id,s.domain,s.generation,s.silver_snapshot_id)
        ON CONFLICT(scope,generation) DO NOTHING;
    SELECT job_id INTO v_job FROM app.gold_job WHERE scope=p_scope AND generation=s.generation;
    RETURN v_job;
END $$;

CREATE OR REPLACE FUNCTION app.queue_gold_after_silver() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- Chờ cuối giao dịch để kết quả làm sạch và nguồn gốc Silver được ghi đầy đủ.
    PERFORM app.enqueue_gold(NEW.scope);
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS queue_gold_after_silver ON app.silver_head;
CREATE CONSTRAINT TRIGGER queue_gold_after_silver
    AFTER INSERT OR UPDATE ON app.silver_head DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION app.queue_gold_after_silver();

-- Bản cũ vẫn được lưu trong lịch sử nhưng không được tính chung với bản hiện hành.
-- Nếu Silver đã đổi mà Gold chưa xong, báo cáo không trả số liệu cũ như dữ liệu mới.
CREATE OR REPLACE VIEW gold.current_metrics AS
    SELECT j.dataset_id,j.domain,s.scope,j.job_id AS gold_snapshot_id,
           j.silver_snapshot_id,j.recipe_version,j.finished_at,
           a.entity,a.metric,a.dimensions,a.value
    FROM app.gold_scope s JOIN app.gold_job j ON j.job_id=s.active_job_id
    JOIN app.silver_head h ON h.scope=s.scope AND h.snapshot_id=j.silver_snapshot_id
    JOIN gold.aggregate a ON a.job_id=j.job_id
    WHERE j.status='SUCCEEDED' AND j.generation=s.generation;
CREATE OR REPLACE VIEW gold.hr_summary AS SELECT * FROM gold.current_metrics WHERE domain='hr';
CREATE OR REPLACE VIEW gold.academic_summary AS SELECT * FROM gold.current_metrics WHERE domain='academic';
CREATE OR REPLACE VIEW gold.admissions_summary AS SELECT * FROM gold.current_metrics WHERE domain='admissions';
-- Tài khoản đọc báo cáo chỉ thấy các bảng tổng hợp, không đọc hồ sơ nguồn.
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='unilake_reader') THEN
        GRANT USAGE ON SCHEMA gold TO unilake_reader;
        GRANT SELECT ON ALL TABLES IN SCHEMA gold TO unilake_reader;
    END IF;
END $$;
COMMIT;
