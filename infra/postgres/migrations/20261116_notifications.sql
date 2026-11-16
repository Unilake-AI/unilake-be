-- Thông báo lấy từ sự kiện xử lý lỗi; trạng thái đã đọc lưu riêng cho từng người.
BEGIN;
CREATE TABLE IF NOT EXISTS app.notification_read (
    app_user_id bigint NOT NULL REFERENCES app.app_user(app_user_id),
    event_id bigint NOT NULL REFERENCES app.data_quality_issue_event(event_id),
    read_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (app_user_id, event_id)
);
COMMIT;
