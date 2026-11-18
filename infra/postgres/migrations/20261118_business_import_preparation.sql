BEGIN;

ALTER TABLE app.gold_sync_job
    ADD COLUMN IF NOT EXISTS preparation jsonb;

COMMIT;
