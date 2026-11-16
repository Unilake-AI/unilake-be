#!/bin/sh
# Chạy toàn bộ migration theo phụ thuộc: identity/Silver trước auto Silver và Gold.
# Dùng cùng điểm nâng cấp với make db-upgrade cho volume đã tồn tại.
set -eu
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
  --file /opt/migrations/upgrade.psql
