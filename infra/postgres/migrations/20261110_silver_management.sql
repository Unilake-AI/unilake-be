-- Giữ cấu hình Silver trong PostgreSQL để dùng lại sau khi máy chủ khởi động lại.
BEGIN;
CREATE TABLE IF NOT EXISTS app.silver_source_profile (
    profile_id VARCHAR(100) PRIMARY KEY,
    definition JSONB NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    updated_by VARCHAR(100) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS app.silver_activation (
    activation_id VARCHAR(36) PRIMARY KEY,
    scope VARCHAR(120) NOT NULL,
    snapshot_id VARCHAR(36) NOT NULL REFERENCES app.silver_snapshot(snapshot_id),
    previous_snapshot_id VARCHAR(36),
    activated_by VARCHAR(100) NOT NULL,
    activated_at TIMESTAMPTZ NOT NULL
);
COMMIT;
