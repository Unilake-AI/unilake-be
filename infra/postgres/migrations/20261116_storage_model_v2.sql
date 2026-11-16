-- Silver metadata trên MinIO; Gold đồng bộ chi tiết vào bảng nghiệp vụ.
BEGIN;
SET LOCAL TIME ZONE 'UTC';
-- DROP chỉ khi mọi dòng cũ đã được export và kiểm chứng; giữ khóa đến cuối transaction.
DO $$
DECLARE name text; actual_count bigint; actual_fingerprint text; proof record;
BEGIN
 FOREACH name IN ARRAY ARRAY['silver_profile_approval','silver_profile_revision','silver_domain_owner','silver_source_profile','silver_activation','silver_head','silver_snapshot','silver_run','silver_worker_heartbeat'] LOOP
  IF to_regclass('app.'||name) IS NOT NULL THEN
   EXECUTE format('LOCK TABLE app.%I IN ACCESS EXCLUSIVE MODE',name);
   EXECUTE format($query$SELECT count(*),md5(coalesce(string_agg(row_to_json(t)::text,E'\n' ORDER BY row_to_json(t)::text),'')) FROM app.%I t$query$,name) INTO actual_count,actual_fingerprint;
   IF actual_count>0 THEN
    IF to_regclass('app.storage_migration_checkpoint') IS NULL THEN
      RAISE EXCEPTION 'SILVER_METADATA_EXPORT_REQUIRED: %',name;
    END IF;
    SELECT * INTO proof FROM app.storage_migration_checkpoint WHERE table_name=name;
    IF NOT FOUND OR proof.row_count<>actual_count OR proof.fingerprint<>actual_fingerprint THEN
      RAISE EXCEPTION 'SILVER_METADATA_EXPORT_STALE: %',name;
    END IF;
   END IF;
  END IF;
 END LOOP;
END $$;

-- Giai đoạn tương thích: Gold nhận manifest Silver mà không cần bảng Silver.
ALTER TABLE app.gold_job ADD COLUMN IF NOT EXISTS silver_manifest_ref JSONB;
DO $$ BEGIN
 IF to_regclass('app.silver_snapshot') IS NOT NULL THEN
  EXECUTE 'UPDATE app.gold_job j SET silver_manifest_ref=s.manifest_ref::jsonb FROM app.silver_snapshot s WHERE j.silver_snapshot_id=s.snapshot_id AND j.silver_manifest_ref IS NULL';
 END IF;
END $$;
DO $$ DECLARE item record; BEGIN
 FOR item IN SELECT conname,conrelid::regclass AS source FROM pg_constraint
   WHERE contype='f' AND conrelid IN ('app.gold_scope'::regclass,'app.gold_job'::regclass)
     AND confrelid IN (to_regclass('app.silver_head'),to_regclass('app.silver_snapshot'))
 LOOP EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',item.source,item.conname); END LOOP;
END $$;

CREATE OR REPLACE FUNCTION app.enqueue_gold_object(p_scope text,p_snapshot text,p_manifest jsonb)
RETURNS varchar(36) LANGUAGE plpgsql AS $$
DECLARE c record; s record; v_job varchar(36);
BEGIN
    IF p_manifest IS NULL OR p_manifest->>'key' IS NULL THEN RAISE EXCEPTION 'GOLD_SOURCE_EVIDENCE_REQUIRED'; END IF;
    SELECT dataset_id,domain INTO c FROM app.cleaning_run
      WHERE silver_snapshot_id=p_snapshot AND publication_status IN ('PUBLISHED','PUBLISHED_WITH_QUARANTINE')
      ORDER BY finished_at DESC NULLS LAST,run_id LIMIT 1;
    IF c IS NULL OR c.domain NOT IN ('hr','academic','admissions') THEN RETURN NULL; END IF;
    INSERT INTO app.gold_scope(scope,dataset_id,domain,silver_snapshot_id)
      VALUES(p_scope,c.dataset_id,c.domain,p_snapshot)
      ON CONFLICT(scope) DO UPDATE SET dataset_id=EXCLUDED.dataset_id,domain=EXCLUDED.domain,
      silver_snapshot_id=EXCLUDED.silver_snapshot_id,generation=app.gold_scope.generation+1,updated_at=now()
      WHERE app.gold_scope.silver_snapshot_id IS DISTINCT FROM EXCLUDED.silver_snapshot_id;
    SELECT * INTO s FROM app.gold_scope WHERE scope=p_scope FOR UPDATE;
    v_job=gen_random_uuid()::text;
    INSERT INTO app.gold_job(job_id,scope,dataset_id,domain,generation,silver_snapshot_id,silver_manifest_ref)
      VALUES(v_job,p_scope,s.dataset_id,s.domain,s.generation,p_snapshot,p_manifest)
      ON CONFLICT(scope,generation) DO UPDATE SET silver_manifest_ref=EXCLUDED.silver_manifest_ref
      WHERE app.gold_job.silver_manifest_ref IS NULL;
    SELECT job_id INTO v_job FROM app.gold_job WHERE scope=p_scope AND generation=s.generation;
    RETURN v_job;
END $$;

CREATE OR REPLACE VIEW gold.current_metrics AS
 SELECT j.dataset_id,j.domain,s.scope,j.job_id AS gold_snapshot_id,j.silver_snapshot_id,j.recipe_version,j.finished_at,
        a.entity,a.metric,a.dimensions,a.value
 FROM app.gold_scope s JOIN app.gold_job j ON j.job_id=s.active_job_id
 JOIN gold.aggregate a ON a.job_id=j.job_id
 WHERE j.status='SUCCEEDED' AND j.generation=s.generation AND j.silver_snapshot_id=s.silver_snapshot_id;

CREATE TABLE IF NOT EXISTS app.gold_record_identity (
 namespace varchar(100) NOT NULL, entity varchar(100) NOT NULL, source_key varchar(1024) NOT NULL,
 target_key jsonb NOT NULL,target_fingerprint varchar(64) NOT NULL,last_gold_job_id varchar(36) NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(namespace,entity,source_key)
);
CREATE INDEX IF NOT EXISTS ix_gold_record_target ON app.gold_record_identity(entity,target_fingerprint);
CREATE TABLE IF NOT EXISTS app.gold_sync_job (
 gold_job_id varchar(36) PRIMARY KEY REFERENCES app.gold_job(job_id),
 recipe_version varchar(50) NOT NULL DEFAULT 'gold-business-records-v1',
 status varchar(20) NOT NULL DEFAULT 'QUEUED' CHECK(status IN ('QUEUED','RUNNING','SYNCED','BLOCKED','FAILED','SUPERSEDED')),
 source_manifest_ref jsonb,attempt_number integer NOT NULL DEFAULT 0,
 lease_token varchar(36),lease_until timestamptz,next_attempt_at timestamptz NOT NULL DEFAULT now(),
 error_code varchar(100),error_report jsonb,result jsonb,created_at timestamptz NOT NULL DEFAULT now(),finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_gold_sync_queue ON app.gold_sync_job(status,next_attempt_at,created_at);
CREATE OR REPLACE FUNCTION app.enqueue_gold_business_sync() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.status='SUCCEEDED' THEN
  INSERT INTO app.gold_sync_job(gold_job_id) VALUES(NEW.job_id) ON CONFLICT DO NOTHING;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS enqueue_gold_business_sync ON app.gold_job;
CREATE TRIGGER enqueue_gold_business_sync AFTER INSERT OR UPDATE OF status ON app.gold_job
 FOR EACH ROW EXECUTE FUNCTION app.enqueue_gold_business_sync();
INSERT INTO app.gold_sync_job(gold_job_id)
 SELECT j.job_id FROM app.gold_job j JOIN app.gold_scope s ON s.active_job_id=j.job_id
 WHERE j.status='SUCCEEDED' AND j.generation=s.generation ON CONFLICT DO NOTHING;

DO $$ BEGIN
 IF to_regclass('app.silver_head') IS NOT NULL THEN
   EXECUTE 'DROP TRIGGER IF EXISTS queue_gold_after_silver ON app.silver_head';
 END IF;
END $$;
DROP FUNCTION IF EXISTS app.queue_gold_after_silver();
DROP FUNCTION IF EXISTS app.enqueue_gold(text);
DROP TABLE IF EXISTS app.silver_profile_approval;
DROP TABLE IF EXISTS app.silver_profile_revision;
DROP TABLE IF EXISTS app.silver_domain_owner;
DROP TABLE IF EXISTS app.silver_source_profile;
DROP TABLE IF EXISTS app.silver_activation;
DROP TABLE IF EXISTS app.silver_head;
DROP TABLE IF EXISTS app.silver_snapshot;
DROP TABLE IF EXISTS app.silver_run;
DROP TABLE IF EXISTS app.silver_worker_heartbeat;
COMMIT;
