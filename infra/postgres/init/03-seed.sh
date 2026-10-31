#!/bin/sh
set -eu

for script in /opt/seed/scripts/*.sql; do
  psql -v ON_ERROR_STOP=1 --single-transaction \
    --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --file "$script"
done
