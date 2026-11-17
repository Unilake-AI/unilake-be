-- Lịch sử phân tích Manager; dữ liệu tổng hợp, không lưu bản ghi cá nhân.
BEGIN;
CREATE TABLE IF NOT EXISTS app.manager_query_history (
    query_id uuid PRIMARY KEY,
    app_user_id bigint NOT NULL REFERENCES app.app_user(app_user_id),
    domain varchar(30), question varchar(500) NOT NULL,
    status varchar(30) NOT NULL CHECK (status IN ('COMPLETED','NO_DATA','FAILED')),
    dataset_ids jsonb NOT NULL DEFAULT '[]',
    result jsonb NOT NULL DEFAULT '{}',
    error_code varchar(100), duration_ms integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_manager_history_owner ON app.manager_query_history(app_user_id,created_at DESC);
COMMIT;
