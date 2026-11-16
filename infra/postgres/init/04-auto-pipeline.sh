#!/bin/sh
# Database mới dùng cùng một migration với database đang có, tránh hai bản cấu trúc lệch nhau.
set -eu
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
  --file /opt/migrations/20261115_auto_silver_pipeline.sql
