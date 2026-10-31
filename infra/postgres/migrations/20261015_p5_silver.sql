-- Migration bổ sung; chủ động chạy một lần trên cơ sở dữ liệu hiện có.
-- Không có thao tác kho đối tượng nào tham gia giao dịch SQL này.
BEGIN;
CREATE TABLE app.silver_run (
    run_id VARCHAR(36) PRIMARY KEY,
    scope VARCHAR(120) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    base_snapshot VARCHAR(36),
    status VARCHAR(20) NOT NULL,
    manifest_ref TEXT,
    issues TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE app.silver_snapshot (
    snapshot_id VARCHAR(36) PRIMARY KEY REFERENCES app.silver_run(run_id),
    scope VARCHAR(120) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    manifest_ref TEXT NOT NULL,
    CONSTRAINT uq_silver_snapshot_request UNIQUE (scope, request_hash)
);
CREATE TABLE app.silver_head (
    scope VARCHAR(120) PRIMARY KEY,
    snapshot_id VARCHAR(36) REFERENCES app.silver_snapshot(snapshot_id)
);
COMMIT;
