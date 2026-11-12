-- Existing definitions are preserved but NEVER automatically approved.
BEGIN;
ALTER TABLE app.cleaning_run ADD COLUMN IF NOT EXISTS profile_evidence JSON;
CREATE TABLE IF NOT EXISTS app.silver_profile_revision (
    profile_id VARCHAR(100) NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    definition JSONB NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    created_by VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (profile_id, revision)
);
CREATE TABLE IF NOT EXISTS app.silver_domain_owner (
    grant_id VARCHAR(36) PRIMARY KEY,
    domain VARCHAR(63) NOT NULL,
    user_id VARCHAR(100) NOT NULL,
    granted_by VARCHAR(100) NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL,
    reason VARCHAR(2000) NOT NULL,
    revoked_by VARCHAR(100),
    revoked_at TIMESTAMPTZ,
    revocation_reason VARCHAR(2000)
);
CREATE INDEX IF NOT EXISTS ix_app_silver_domain_owner_domain ON app.silver_domain_owner(domain);
CREATE TABLE IF NOT EXISTS app.silver_profile_approval (
    profile_id VARCHAR(100) NOT NULL,
    revision INTEGER NOT NULL,
    grant_id VARCHAR(36) NOT NULL REFERENCES app.silver_domain_owner(grant_id),
    approved_by VARCHAR(100) NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL,
    evidence VARCHAR(2000) NOT NULL,
    allow_partial_publication BOOLEAN NOT NULL,
    revoked_by VARCHAR(100),
    revoked_at TIMESTAMPTZ,
    revocation_reason VARCHAR(2000),
    PRIMARY KEY (profile_id, revision),
    FOREIGN KEY (profile_id, revision) REFERENCES app.silver_profile_revision(profile_id, revision)
);
INSERT INTO app.silver_profile_revision
    (profile_id, revision, definition, fingerprint, created_by, created_at)
SELECT profile_id, revision, definition, fingerprint, updated_by, updated_at
FROM app.silver_source_profile ON CONFLICT DO NOTHING;
CREATE OR REPLACE FUNCTION app.reject_silver_profile_revision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'SILVER_PROFILE_REVISION_IMMUTABLE';
END;
$$;
DROP TRIGGER IF EXISTS silver_profile_revision_immutable ON app.silver_profile_revision;
CREATE TRIGGER silver_profile_revision_immutable
BEFORE UPDATE OR DELETE ON app.silver_profile_revision
FOR EACH ROW EXECUTE FUNCTION app.reject_silver_profile_revision_mutation();
COMMIT;
