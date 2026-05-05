#!/bin/sh
SECRETS_FILE="/etc/vault/secrets/secrets.env"
for i in $(seq 1 30); do
  [ -f "$SECRETS_FILE" ] && break
  sleep 1
done
if [ -f "$SECRETS_FILE" ]; then
  set -a
  . "$SECRETS_FILE"
  set +a
fi

# Ensure data directory exists for SQLite
mkdir -p /app/data

# Run database migrations
alembic upgrade head

exec python api.py
