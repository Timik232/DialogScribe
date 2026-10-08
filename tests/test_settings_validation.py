"""Fail-closed secrets validation and the /v1 availability/auth matrix.

Uses the module-reload pattern: set env → importlib.reload(modules) →
assert → (autouse teardown) restore env and reload again, so global module
state is clean for every other test module regardless of failure.
"""

import importlib
import io
import logging
import os
import secrets as secrets_module
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

STRONG_JWT = "jwt-" + "a" * 60
STRONG_KEY = "key-" + "b" * 60
# Dummy explicit DB URL for production-mode tests (not a secret, never real).
PROD_DB_URL = "postgresql+asyncpg://ci:ci@localhost:5432/dialogscribe_ci"
TRACKED_VARS = ("ENVIRONMENT", "JWT_SECRET", "API_KEY", "V1_API_ENABLED", "DATABASE_URL")


def _with_env(**overrides: str | None) -> None:
    """Apply overrides on a cleared secrets env (None removes the var)."""
    for key in TRACKED_VARS:
        os.environ.pop(key, None)
    for key, value in overrides.items():
        if value is not None:
            os.environ[key] = value


@pytest.fixture(autouse=True)
def _restore_modules_after_test():
    saved = {key: os.environ.get(key) for key in TRACKED_VARS}
    # Import the full chain under the ambient env first: a settings import
    # that raises would evict the module from sys.modules, making the plain
    # `import` statements below re-execute the body outside pytest.raises.
    reload_modules()
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    reload_modules()


def reload_modules():
    import api
    import gigaam_transcriber.auth
    import gigaam_transcriber.settings
    import routers._helpers

    importlib.reload(gigaam_transcriber.settings)
    importlib.reload(gigaam_transcriber.auth)
    importlib.reload(routers._helpers)
    importlib.reload(api)
    return gigaam_transcriber.settings, routers._helpers, api


def _bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def _audio_file():
    return {"file": ("test.wav", io.BytesIO(b"fake audio"), "audio/wav")}


@contextmanager
def _client(api_module):
    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = MagicMock(text="Hello world")
    with (
        patch.object(api_module, "GigaAMTranscriber", return_value=mock_transcriber),
        TestClient(api_module.app) as client,
    ):
        yield client, mock_transcriber


class TestProductionValidation:
    @pytest.mark.parametrize("weak_jwt", [None, "", "ci-test-only"])
    def test_production_aborts_for_missing_or_short_jwt_secret(self, weak_jwt):
        _with_env(ENVIRONMENT="production", JWT_SECRET=weak_jwt)
        import gigaam_transcriber.settings as settings_module

        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            importlib.reload(settings_module)

    def test_production_abort_message_does_not_leak_secret_value(self):
        _with_env(ENVIRONMENT="production", JWT_SECRET="super-secret-value-do-not-log")
        import gigaam_transcriber.settings as settings_module

        with pytest.raises(RuntimeError) as excinfo:
            importlib.reload(settings_module)
        assert "super-secret-value-do-not-log" not in str(excinfo.value)

    @pytest.mark.parametrize(
        ("api_key", "reason"),
        [
            (None, "API_KEY is not set"),
            ("k" * 8, "API_KEY must be at least 32 characters"),
            (STRONG_JWT, "API_KEY must differ from JWT_SECRET"),
        ],
    )
    def test_production_aborts_for_invalid_api_key_when_v1_enabled(self, api_key, reason):
        _with_env(ENVIRONMENT="production", JWT_SECRET=STRONG_JWT, V1_API_ENABLED="true", API_KEY=api_key)
        import gigaam_transcriber.settings as settings_module

        with pytest.raises(RuntimeError, match=reason):
            importlib.reload(settings_module)

    def test_production_accepts_strong_distinct_secrets(self):
        _with_env(
            ENVIRONMENT="production",
            JWT_SECRET=STRONG_JWT,
            V1_API_ENABLED="true",
            API_KEY=STRONG_KEY,
            DATABASE_URL=PROD_DB_URL,
        )
        settings_module, _, _ = reload_modules()

        assert settings_module.JWT_SECRET == STRONG_JWT
        assert settings_module.API_KEY == STRONG_KEY
        assert settings_module.V1_API_ENABLED is True


class TestDevelopmentValidation:
    def test_development_warns_but_does_not_abort_for_short_jwt_secret(self, caplog):
        _with_env(ENVIRONMENT="development", JWT_SECRET="ci-test-only")
        import gigaam_transcriber.settings as settings_module

        with caplog.at_level(logging.WARNING, logger="dialogscribe.settings"):
            importlib.reload(settings_module)

        assert settings_module.JWT_SECRET == "ci-test-only"
        assert any("JWT_SECRET" in record.message for record in caplog.records)

    def test_development_warns_but_does_not_abort_for_equal_secrets(self, caplog):
        _with_env(
            ENVIRONMENT="development",
            JWT_SECRET=STRONG_JWT,
            V1_API_ENABLED="true",
            API_KEY=STRONG_JWT,
        )
        import gigaam_transcriber.settings as settings_module

        with caplog.at_level(logging.WARNING, logger="dialogscribe.settings"):
            importlib.reload(settings_module)

        assert any("API_KEY must differ" in record.message for record in caplog.records)

    def test_unset_environment_stays_importable_with_short_ci_secret(self, caplog):
        _with_env(JWT_SECRET="ci-test-only")
        import gigaam_transcriber.settings as settings_module

        with caplog.at_level(logging.WARNING, logger="dialogscribe.settings"):
            importlib.reload(settings_module)

        assert settings_module.JWT_SECRET == "ci-test-only"

    def test_development_with_strong_secrets_emits_no_secret_warnings(self, caplog):
        _with_env(ENVIRONMENT="development", JWT_SECRET=STRONG_JWT, V1_API_ENABLED="true", API_KEY=STRONG_KEY)
        import gigaam_transcriber.settings as settings_module

        with caplog.at_level(logging.WARNING, logger="dialogscribe.settings"):
            importlib.reload(settings_module)

        assert not [r for r in caplog.records if "Insecure secrets configuration" in r.message]


class TestDatabaseUrlValidation:
    @pytest.mark.parametrize("missing", [None, "", "   "])
    def test_production_aborts_for_missing_or_empty_database_url(self, missing):
        _with_env(ENVIRONMENT="production", JWT_SECRET=STRONG_JWT, DATABASE_URL=missing)
        import gigaam_transcriber.settings as settings_module

        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            importlib.reload(settings_module)

    def test_production_abort_message_instructs_explicit_database_url(self):
        _with_env(ENVIRONMENT="production", JWT_SECRET=STRONG_JWT, DATABASE_URL=None)
        import gigaam_transcriber.settings as settings_module

        with pytest.raises(RuntimeError) as excinfo:
            importlib.reload(settings_module)
        message = str(excinfo.value)
        assert "set DATABASE_URL explicitly" in message
        assert "dialogscribe-dev.db" in message  # names the refused fallback, not a value

    def test_production_reports_secrets_problems_before_database_url(self):
        _with_env(ENVIRONMENT="production", JWT_SECRET="short", DATABASE_URL=None)
        import gigaam_transcriber.settings as settings_module

        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            importlib.reload(settings_module)

    def test_development_warns_but_does_not_abort_for_missing_database_url(self, caplog):
        _with_env(ENVIRONMENT="development", JWT_SECRET=STRONG_JWT, DATABASE_URL=None)
        import gigaam_transcriber.settings as settings_module

        with caplog.at_level(logging.WARNING, logger="dialogscribe.settings"):
            importlib.reload(settings_module)

        assert any("DATABASE_URL" in record.message for record in caplog.records)

    def test_unset_environment_warns_but_stays_importable(self, caplog):
        _with_env(JWT_SECRET="ci-test-only", DATABASE_URL=None)
        import gigaam_transcriber.settings as settings_module

        with caplog.at_level(logging.WARNING, logger="dialogscribe.settings"):
            importlib.reload(settings_module)

        assert any("DATABASE_URL" in record.message for record in caplog.records)


class TestJwtSecretIndependence:
    def test_jwt_secret_no_longer_falls_back_to_api_key(self):
        _with_env(JWT_SECRET=None, API_KEY=STRONG_KEY)
        settings_module, _, _ = reload_modules()
        import gigaam_transcriber.auth as auth_module

        assert settings_module.JWT_SECRET == ""
        assert auth_module.JWT_SECRET == ""
        assert auth_module.JWT_SECRET != STRONG_KEY


class TestV1Matrix:
    def test_v1_disabled_returns_503_without_processing(self):
        _with_env(
            ENVIRONMENT="production",
            JWT_SECRET=STRONG_JWT,
            API_KEY=STRONG_KEY,
            DATABASE_URL=PROD_DB_URL,
        )
        _, _, api_module = reload_modules()

        with _client(api_module) as (client, mock_transcriber):
            resp = client.post("/v1/audio/transcriptions", files=_audio_file(), headers=_bearer(STRONG_KEY))
            assert resp.status_code == 503
            assert resp.json()["detail"]["error"]["code"] == 503
            mock_transcriber.transcribe.assert_not_called()

    def test_v1_enabled_without_api_key_returns_503(self):
        _with_env(
            ENVIRONMENT="development",
            JWT_SECRET=STRONG_JWT,
            V1_API_ENABLED="true",
            API_KEY=None,
        )
        _, _, api_module = reload_modules()

        with _client(api_module) as (client, _):
            resp = client.post("/v1/audio/transcriptions", files=_audio_file())
            assert resp.status_code == 503

    @pytest.mark.parametrize("bad_auth", [None, "wrong-key", "k" * 60])
    def test_v1_enabled_missing_or_wrong_bearer_returns_401(self, bad_auth):
        _with_env(
            ENVIRONMENT="production",
            JWT_SECRET=STRONG_JWT,
            V1_API_ENABLED="true",
            API_KEY=STRONG_KEY,
            DATABASE_URL=PROD_DB_URL,
        )
        _, _, api_module = reload_modules()

        with _client(api_module) as (client, mock_transcriber):
            headers = _bearer(bad_auth) if bad_auth is not None else {}
            resp = client.post("/v1/audio/transcriptions", files=_audio_file(), headers=headers)
            assert resp.status_code == 401
            assert resp.json()["detail"]["error"]["code"] == 401
            mock_transcriber.transcribe.assert_not_called()

    def test_v1_enabled_correct_bearer_returns_200(self):
        _with_env(
            ENVIRONMENT="production",
            JWT_SECRET=STRONG_JWT,
            V1_API_ENABLED="true",
            API_KEY=STRONG_KEY,
            DATABASE_URL=PROD_DB_URL,
        )
        _, _, api_module = reload_modules()

        with _client(api_module) as (client, _):
            resp = client.post(
                "/v1/audio/transcriptions", files=_audio_file(), headers=_bearer(STRONG_KEY)
            )
            assert resp.status_code == 200
            assert resp.json()["text"] == "Hello world"


class TestCompareDigestUsage:
    def test_verify_auth_compares_keys_with_compare_digest(self, monkeypatch):
        _with_env(
            ENVIRONMENT="development",
            JWT_SECRET=STRONG_JWT,
            V1_API_ENABLED="true",
            API_KEY=STRONG_KEY,
        )
        _, helpers_module, _ = reload_modules()

        calls: list[tuple[str, str]] = []
        real_compare = secrets_module.compare_digest

        def spy(a, b):
            calls.append((a, b))
            return real_compare(a, b)

        monkeypatch.setattr(helpers_module.secrets, "compare_digest", spy)

        assert calls == []
        helpers_module._verify_auth(HTTPAuthorizationCredentials(scheme="Bearer", credentials=STRONG_KEY))
        assert calls == [(STRONG_KEY, STRONG_KEY)]

        with pytest.raises(HTTPException) as excinfo:
            helpers_module._verify_auth(
                HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong-key")
            )
        assert excinfo.value.status_code == 401
        assert len(calls) == 2

    def test_helpers_source_uses_compare_digest_not_plain_equality(self):
        import inspect

        _, helpers_module, _ = reload_modules()
        source = inspect.getsource(helpers_module._verify_auth)

        assert "compare_digest" in source
        assert "credentials.credentials != API_KEY" not in source
        assert "credentials.credentials == API_KEY" not in source
