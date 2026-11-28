BEGIN;

CREATE TABLE IF NOT EXISTS app.iam_event (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id BIGINT NOT NULL REFERENCES app.app_user(app_user_id),
    action VARCHAR(40) NOT NULL,
    target_id BIGINT NOT NULL,
    occurred_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_iam_event_recent ON app.iam_event (occurred_at DESC, event_id DESC);

COMMIT;
