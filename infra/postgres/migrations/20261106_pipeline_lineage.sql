-- Migration bổ sung cho theo dõi Data Lineage (UDL-176 & UDL-20).
BEGIN;

CREATE TABLE IF NOT EXISTS app.pipeline_lineage (
    lineage_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_layer VARCHAR(20) NOT NULL,
    source_id VARCHAR(255) NOT NULL,
    source_name VARCHAR(255) NOT NULL,
    target_layer VARCHAR(20) NOT NULL,
    target_id VARCHAR(255) NOT NULL,
    target_name VARCHAR(255) NOT NULL,
    job_id BIGINT,
    lineage_type VARCHAR(50) NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_pipeline_lineage_target ON app.pipeline_lineage (target_id);
CREATE INDEX IF NOT EXISTS ix_pipeline_lineage_source ON app.pipeline_lineage (source_id);
CREATE INDEX IF NOT EXISTS ix_pipeline_lineage_job ON app.pipeline_lineage (job_id);

COMMIT;
