#!/bin/sh
# Dùng cùng cấu trúc cho cơ sở dữ liệu mới và cơ sở dữ liệu đang sử dụng.
set -eu
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
  --file /opt/migrations/20261116_gold_pipeline.sql
