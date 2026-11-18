-- Additive workflow state. Historical enrollment decisions remain unknown (NULL).
BEGIN;
ALTER TABLE app.admission ADD COLUMN IF NOT EXISTS enrollment_status varchar(30);
ALTER TABLE app.admission ADD COLUMN IF NOT EXISTS enrollment_date date;
ALTER TABLE app.admission ADD COLUMN IF NOT EXISTS revision bigint NOT NULL DEFAULT 1;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='app.admission'::regclass AND conname='ck_admission_enrollment_status') THEN
        ALTER TABLE app.admission ADD CONSTRAINT ck_admission_enrollment_status
            CHECK (enrollment_status IS NULL OR enrollment_status IN ('PENDING','CONFIRMED','DECLINED','WITHDRAWN'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='app.admission'::regclass AND conname='ck_admission_confirmation') THEN
        ALTER TABLE app.admission ADD CONSTRAINT ck_admission_confirmation
            CHECK (enrollment_status IS DISTINCT FROM 'CONFIRMED' OR
                (upper(admission_status)='ACCEPTED' AND enrollment_date IS NOT NULL));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='app.admission'::regclass AND conname='ck_admission_revision') THEN
        ALTER TABLE app.admission ADD CONSTRAINT ck_admission_revision CHECK (revision > 0);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS ix_admission_enrollment_status ON app.admission(enrollment_status);
CREATE INDEX IF NOT EXISTS ix_applicant_source_target
    ON app.gold_record_identity(namespace,(target_key->>'applicant_id'),source_key)
    WHERE entity='applicant';

CREATE TABLE IF NOT EXISTS app.admission_correction (
    correction_id varchar(36) PRIMARY KEY,
    dataset_id bigint NOT NULL REFERENCES app.dataset(dataset_id),
    admission_id bigint NOT NULL REFERENCES app.admission(admission_id),
    expected_revision bigint NOT NULL CHECK (expected_revision > 0),
    operation varchar(40) NOT NULL CHECK (operation IN ('RESULT_CORRECTION','ENROLLMENT_CORRECTION','RESTORE_ENROLLMENT','RECONCILE_LEGACY')),
    changes jsonb NOT NULL,
    reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 2000),
    status varchar(20) NOT NULL DEFAULT 'REQUESTED' CHECK (status IN ('REQUESTED','APPLIED','CANCELLED')),
    requested_by bigint NOT NULL REFERENCES app.app_user(app_user_id),
    applied_by bigint REFERENCES app.app_user(app_user_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    applied_at timestamptz,
    result jsonb
);
CREATE INDEX IF NOT EXISTS ix_admission_correction_target ON app.admission_correction(admission_id,created_at);
CREATE TABLE IF NOT EXISTS app.admission_event (
    event_id varchar(36) PRIMARY KEY DEFAULT gen_random_uuid()::text,
    admission_id bigint NOT NULL REFERENCES app.admission(admission_id),
    old_revision bigint,
    new_revision bigint NOT NULL,
    operation varchar(40) NOT NULL,
    actor_id bigint REFERENCES app.app_user(app_user_id),
    dataset_id bigint REFERENCES app.dataset(dataset_id),
    gold_job_id varchar(36) REFERENCES app.gold_job(job_id),
    correction_id varchar(36) REFERENCES app.admission_correction(correction_id),
    request_key varchar(64),
    reason text,
    source jsonb NOT NULL DEFAULT '{}',
    before_values jsonb NOT NULL,
    after_values jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_admission_event_target ON app.admission_event(admission_id,new_revision);
CREATE INDEX IF NOT EXISTS ix_admission_event_request ON app.admission_event(admission_id,request_key);

-- Every database writer advances the same revision; a no-op never advances it.
CREATE OR REPLACE FUNCTION app.admission_revision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='INSERT' THEN NEW.revision=1;
    ELSIF (to_jsonb(NEW)-'revision') IS DISTINCT FROM (to_jsonb(OLD)-'revision') THEN
        NEW.revision=OLD.revision+1;
    ELSE NEW.revision=OLD.revision;
    END IF;
    RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION app.audit_admission_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE evidence jsonb;
BEGIN
    IF TG_OP='UPDATE' AND NEW.revision=OLD.revision THEN RETURN NEW; END IF;
    evidence=coalesce(nullif(current_setting('unilake.admission_context',true),'')::jsonb,'{}'::jsonb);
    IF evidence->>'admission_id' IS NOT NULL AND (evidence->>'admission_id')::bigint<>NEW.admission_id THEN
        evidence='{}'::jsonb;
    END IF;
    INSERT INTO app.admission_event(admission_id,old_revision,new_revision,operation,
        actor_id,dataset_id,gold_job_id,correction_id,request_key,reason,source,before_values,after_values)
    VALUES (NEW.admission_id,CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.revision END,NEW.revision,
        coalesce(evidence->>'operation','UNATTRIBUTED'),(evidence->>'actor_id')::bigint,
        (evidence->>'dataset_id')::bigint,evidence->>'gold_job_id',evidence->>'correction_id',
        evidence->>'request_key',evidence->>'reason',coalesce(evidence->'source','{}'::jsonb),
        CASE WHEN TG_OP='INSERT' THEN '{}'::jsonb ELSE to_jsonb(OLD) END,to_jsonb(NEW));
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS admission_revision_guard ON app.admission;
CREATE TRIGGER admission_revision_guard BEFORE INSERT OR UPDATE ON app.admission
    FOR EACH ROW EXECUTE FUNCTION app.admission_revision_guard();
DROP TRIGGER IF EXISTS audit_admission_change ON app.admission;
CREATE TRIGGER audit_admission_change AFTER INSERT OR UPDATE ON app.admission
    FOR EACH ROW EXECUTE FUNCTION app.audit_admission_change();
COMMIT;
