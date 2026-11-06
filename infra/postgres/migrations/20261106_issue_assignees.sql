BEGIN;
SET LOCAL search_path TO app, public;

-- Yêu cầu cũ giữ NULL để Data Admin chọn người phụ trách.
ALTER TABLE data_quality_issue
    ADD COLUMN IF NOT EXISTS assignee_user_id BIGINT REFERENCES app_user(app_user_id);

CREATE INDEX IF NOT EXISTS ix_quality_issue_assignee_updated
    ON data_quality_issue(assignee_user_id, updated_at DESC);

COMMIT;
