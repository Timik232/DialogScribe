#!/usr/bin/env python3
"""Validate Vault-rendered JSON and emit a shell-safe environment stream."""

import json
import os
import sys
from pathlib import Path
from typing import cast

ALLOWLIST = {
    "DATABASE_URL", "JWT_SECRET", "API_KEY", "V1_API_ENABLED", "ENVIRONMENT",
    "ADMIN_PASSWORD", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "OPENAI_API_KEY",
    "MISTRAL_API_KEY", "HF_TOKEN", "SMTP_HOST", "SMTP_PORT", "SMTP_USER",
    "SMTP_PASSWORD", "SMTP_FROM", "FRONTEND_URL", "HOST", "PORT",
    "MAX_UPLOAD_SIZE_MB", "MAX_SAVED_PAYLOAD_MB", "LLM_CLASSIFIER_MODEL",
    "LLM_ADVISOR_MODEL", "LLM_MODELS", "ADMIN_EMAIL", "ASR_URL", "ASR_MODEL",
    "PROXY_URL", "ASR_MIN_INTERVAL", "USAGE_RETENTION_DAYS", "SMTP_USE_TLS",
    "LITELLM_URL", "LITELLM_MODEL", "LITELLM_API_KEY", "CHAT_CACHE_MAX_ENTRIES",
    "CHAT_CACHE_TTL_SECONDS", "FFMPEG_TIMEOUT_SECONDS", "FFPROBE_TIMEOUT_SECONDS",
    "SUBPROCESS_MAX_OUTPUT_BYTES", "LLM_LOG_FILE",
}


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def main() -> int:
    path = Path(os.environ.get("VAULT_SECRETS_FILE", "/etc/vault/secrets/secrets.json"))
    if not path.is_file():
        print("entrypoint secrets: secrets file is missing", file=sys.stderr)
        return 1
    try:
        data = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError):
        print("entrypoint secrets: secrets file is not valid JSON", file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print("entrypoint secrets: secrets JSON must be an object", file=sys.stderr)
        return 1
    if not data:
        print("entrypoint secrets: secrets JSON object is empty", file=sys.stderr)
        return 1
    values = cast(dict[object, object], data)
    for key, value in values.items():
        if key in ALLOWLIST and isinstance(value, (str, int, float, bool)):
            print(f"{key}={shell_quote(str(value))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
