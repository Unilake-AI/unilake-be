-- Per-record history for contract-based imports; written with the domain transaction.
BEGIN;
-- New jobs use v2; queued/historical v1 jobs retain their frozen recipe.
ALTER TABLE app.gold_job ALTER COLUMN recipe_version SET DEFAULT 'group-counts-v2';
ALTER TABLE app.gold_job ALTER COLUMN recipe_fingerprint SET DEFAULT
    '8296dc2f7ef55e24691ce2b02e892fea470b25dbf09f7d8ff5e2a995502a17e3';
CREATE TABLE IF NOT EXISTS app.business_import_change (
    gold_job_id varchar(36) NOT NULL,
    entity varchar(100) NOT NULL,
    target_fingerprint varchar(64) NOT NULL,
    target_key jsonb NOT NULL,
    action varchar(12) NOT NULL CHECK (action IN ('inserted','updated','unchanged')),
    evidence jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (gold_job_id, entity, target_fingerprint),
    FOREIGN KEY (gold_job_id) REFERENCES app.gold_job(job_id)
);
CREATE INDEX IF NOT EXISTS ix_business_import_change_target
    ON app.business_import_change(entity,target_fingerprint,created_at);
COMMIT;
