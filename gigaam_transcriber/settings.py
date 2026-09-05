"""Validated runtime settings and fail-closed secret checks.

Security-critical environment variables (``ENVIRONMENT``, ``JWT_SECRET``,
``API_KEY``, ``V1_API_ENABLED``) are read here and nowhere else.

Import-time validation:

* ``ENVIRONMENT=production`` (explicitly declared — docker-compose.yaml sets
  it) aborts the import with ``RuntimeError`` when ``JWT_SECRET`` is
  missing/short, or when ``V1_API_ENABLED`` is enabled without a strong,
  distinct ``API_KEY``. A failed import means application startup exits
  non-zero instead of serving with weak credentials.
* ``ENVIRONMENT=development`` or an unset ``ENVIRONMENT`` runs the same
  checks but only logs warnings, so local/test bootstrap with short
  throwaway secrets (CI uses ``JWT_SECRET=ci-test-only``) stays usable.

Secret values never appear in exception messages or log records — only the
name of the offending variable and the reason.
"""

from __future__ import annotations

import logging
import os
import secrets

logger = logging.getLogger("dialogscribe.settings")

MIN_SECRET_LENGTH = 32


def environment() -> str:
    """Current environment mode; anything but ``development`` is production."""
    return os.getenv("ENVIRONMENT", "production").strip().lower()


def is_development() -> bool:
    """True only for an explicit ``ENVIRONMENT=development`` (fail-safe)."""
    return environment() == "development"


def _strict_secret_validation() -> bool:
    """Abort on weak secrets only when production was explicitly declared.

    An unset ``ENVIRONMENT`` keeps warn-only validation so CI/local test runs
    with short throwaway secrets stay importable; real deployments declare
    ``ENVIRONMENT=production`` (docker-compose.yaml does), where weak secrets
    abort startup.
    """
    return os.getenv("ENVIRONMENT", "").strip().lower() == "production"


def _is_truthy(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _secret_problems(name: str, value: str) -> list[str]:
    if not value:
        return [f"{name} is not set"]
    if len(value) < MIN_SECRET_LENGTH:
        return [f"{name} must be at least {MIN_SECRET_LENGTH} characters"]
    return []


def _validate_secrets(*, v1_enabled: bool, strict: bool) -> tuple[str, str]:
    """Validate secret strength; abort (strict) or warn, never leak values."""
    jwt_secret = os.getenv("JWT_SECRET", "")
    api_key = os.getenv("API_KEY", "")

    problems = _secret_problems("JWT_SECRET", jwt_secret)
    if v1_enabled:
        problems.extend(_secret_problems("API_KEY", api_key))
        if jwt_secret and api_key and secrets.compare_digest(api_key, jwt_secret):
            problems.append("API_KEY must differ from JWT_SECRET")

    if not problems:
        return jwt_secret, api_key

    summary = "; ".join(problems)
    if strict:
        raise RuntimeError(f"invalid secrets configuration: {summary}")
    logger.warning(
        "Insecure secrets configuration (warn-only outside explicit "
        "ENVIRONMENT=production): %s",
        summary,
    )
    return jwt_secret, api_key


if "ENVIRONMENT" not in os.environ:
    logger.warning(
        "ENVIRONMENT is not set: production posture applies to docs/schema "
        "gating, but secret validation stays warn-only. Set "
        "ENVIRONMENT=production in real deployments to fail closed."
    )

V1_API_ENABLED = _is_truthy(os.getenv("V1_API_ENABLED", "false"))
JWT_SECRET, API_KEY = _validate_secrets(
    v1_enabled=V1_API_ENABLED, strict=_strict_secret_validation()
)
