BEGIN;
SET LOCAL search_path TO app, public;

CREATE TABLE IF NOT EXISTS pipeline_lineage (
    lineage_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_layer VARCHAR(50) NOT NULL,
    source_name TEXT NOT NULL,
    target_layer VARCHAR(50) NOT NULL,
    target_name TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_pipeline_lineage_source
    ON pipeline_lineage(source_layer, source_name);
CREATE INDEX IF NOT EXISTS ix_pipeline_lineage_target
    ON pipeline_lineage(target_layer, target_name);
CREATE INDEX IF NOT EXISTS ix_pipeline_lineage_created
    ON pipeline_lineage(created_at DESC);
CREATE INDEX IF NOT EXISTS ix_pipeline_lineage_metadata
    ON pipeline_lineage USING GIN (metadata jsonb_path_ops);

COMMIT;
