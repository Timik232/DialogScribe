#!/bin/sh
# Vault-sourced secrets (secrets.env.tmpl keys: MISTRAL_API_KEY, HF_TOKEN,
# LLM_*, JWT_SECRET, API_KEY, GRADIO_*). ENVIRONMENT / V1_API_ENABLED are
# plain configuration and are never sourced here, so compose-provided values
# stand. Strength validation is fail-closed inside the app (settings.py):
# with ENVIRONMENT=production a missing/weak secret aborts startup below.
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
