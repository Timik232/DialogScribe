#!/bin/sh
# Vault renders inert JSON; only the validated allowlist is imported.
SECRETS_FILE="${VAULT_SECRETS_FILE:-/etc/vault/secrets/secrets.json}"
for i in $(seq 1 30); do
  [ -f "$SECRETS_FILE" ] && break
  sleep 1
done
if [ ! -f "$SECRETS_FILE" ]; then
  echo "entrypoint: secrets file was not rendered before timeout" >&2
  exit 1
fi

SECRETS_OUTPUT=$(mktemp)
trap 'rm -f "$SECRETS_OUTPUT"' EXIT
if ! python3 /app/scripts/entrypoint_secrets.py > "$SECRETS_OUTPUT"; then
  echo "entrypoint: secrets validation failed; refusing to start" >&2
  exit 1
fi
while IFS= read -r line; do
  key=${line%%=*}
  encoded=${line#*=}
  value=$(python3 - "$encoded" <<'PY'
import sys

encoded = sys.argv[1]
if len(encoded) >= 2 and encoded[0] == "'" and encoded[-1] == "'":
    encoded = encoded[1:-1]
print(encoded.replace(chr(39) + "\\" + chr(39) + chr(39), chr(39)), end="")
PY
  ) || exit 1
  export "$key=$value"
done < "$SECRETS_OUTPUT"
rm -f "$SECRETS_OUTPUT"
trap - EXIT

# Ensure data directory exists for SQLite
mkdir -p /app/data

# Run database migrations
alembic upgrade head

exec python api.py
