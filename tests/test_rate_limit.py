"""In-memory token-bucket rate limiting: 429 + Retry-After, refill recovery,
cross-identity independence, env tunables (Task 7).
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gigaam_transcriber import rate_limit
from gigaam_transcriber.rate_limit import BucketSpec, TokenBucket, enforce, reset_all


@pytest.fixture(autouse=True)
def _clean_buckets(monkeypatch):
    monkeypatch.delenv("RATE_LIMIT_LOGIN", raising=False)
    monkeypatch.delenv("RATE_LIMIT_REGISTER", raising=False)
    monkeypatch.delenv("RATE_LIMIT_FORGOT_PASSWORD", raising=False)
    monkeypatch.delenv("RATE_LIMIT_RESET_PASSWORD", raising=False)
    reset_all()
    yield
    reset_all()


@pytest.fixture
def client():
    mock_transcriber = MagicMock()
    with patch("api.GigaAMTranscriber", return_value=mock_transcriber):
        from api import app
        from gigaam_transcriber.auth import get_current_user
        from gigaam_transcriber.database import get_db

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: None, first=lambda: None)
        )
        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[get_current_user] = lambda: MagicMock(id="rate-user-1")
        with TestClient(app) as c:
            yield c
        app.dependency_overrides.clear()


def _login(client, **kwargs):
    return client.post("/api/auth/login", json={"login": "u@x.io", "password": "whatever1"}, **kwargs)


class TestTokenBucketUnit:
    def test_capacity_and_deterministic_refill(self):
        bucket = TokenBucket(BucketSpec(2, 60), now=100.0)
        assert bucket.try_consume(100.0) == 0.0
        assert bucket.try_consume(100.5) == 0.0
        retry = bucket.try_consume(100.9)
        assert 0.0 < retry <= 1.0
        assert bucket.try_consume(101.0) == 0.0

    def test_refill_never_exceeds_capacity(self):
        bucket = TokenBucket(BucketSpec(1, 600), now=0.0)
        assert bucket.try_consume(0.0) == 0.0
        assert bucket.try_consume(3600.0) == 0.0
        assert bucket.try_consume(3600.1) > 0.0

    def test_enforce_raises_429_with_retry_after(self):
        with patch.dict(rate_limit.DEFAULT_BUCKETS, {"login": BucketSpec(1, 60)}):
            enforce("login", "unit-ip")
            with pytest.raises(Exception) as excinfo:
                enforce("login", "unit-ip")
            assert excinfo.value.status_code == 429
            assert int(excinfo.value.headers["Retry-After"]) >= 1
            assert excinfo.value.detail == rate_limit.TOO_MANY_REQUESTS_DETAIL

    def test_bodies_are_generic(self):
        assert rate_limit.TOO_MANY_REQUESTS_DETAIL == "Too many requests, please try again later"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_LOGIN", "2")
        enforce("login", "env-ip")
        enforce("login", "env-ip")
        with pytest.raises(Exception) as excinfo:
            enforce("login", "env-ip")
        assert excinfo.value.status_code == 429

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_LOGIN", "not-a-number")
        assert rate_limit.spec_for("login") == rate_limit.DEFAULT_BUCKETS["login"]


class TestAuthEndpointLimits:
    def test_login_429_after_5_with_retry_after(self, client):
        for _ in range(5):
            assert _login(client).status_code == 401
        sixth = _login(client)
        assert sixth.status_code == 429
        assert int(sixth.headers["Retry-After"]) >= 1
        assert sixth.json()["detail"] == "Too many requests, please try again later"

    def test_register_429_after_3(self, client):
        for _ in range(3):
            resp = client.post(
                "/api/auth/register",
                json={"email": "a@b.io", "username": "ab", "password": "password123"},
            )
            assert resp.status_code in (201, 409, 422)
        assert (
            client.post(
                "/api/auth/register",
                json={"email": "a@b.io", "username": "ab", "password": "password123"},
            ).status_code
            == 429
        )

    def test_forgot_429_after_3(self, client):
        for _ in range(3):
            assert client.post("/api/auth/forgot-password", json={"email": "a@b.io"}).status_code == 200
        assert client.post("/api/auth/forgot-password", json={"email": "a@b.io"}).status_code == 429

    def test_reset_429_after_5(self, client):
        for _ in range(5):
            assert client.post(
                "/api/auth/reset-password", json={"token": "x", "new_password": "password123"}
            ).status_code in (400, 422)
        assert (
            client.post(
                "/api/auth/reset-password", json={"token": "x", "new_password": "password123"}
            ).status_code
            == 429
        )

    def test_refresh_429_after_30(self, client):
        for _ in range(30):
            assert client.post("/api/auth/refresh").status_code == 401
        assert client.post("/api/auth/refresh").status_code == 429


class TestIndependenceAndRecovery:
    def test_second_ip_unaffected(self, client):
        for _ in range(6):
            _login(client, headers={"X-Forwarded-For": "10.0.0.1"})
        blocked = _login(client, headers={"X-Forwarded-For": "10.0.0.1"})
        assert blocked.status_code == 429

        other = _login(client, headers={"X-Forwarded-For": "10.0.0.2"})
        assert other.status_code == 401

    def test_refill_recovery(self, client, monkeypatch):
        monkeypatch.setitem(
            rate_limit.DEFAULT_BUCKETS, "login", BucketSpec(1, 60)
        )
        assert _login(client).status_code == 401
        assert _login(client).status_code == 429
        time.sleep(1.2)
        assert _login(client).status_code == 401

    def test_user_buckets_independent_for_chat(self, client):
        from api import app
        from gigaam_transcriber.auth import get_current_user

        payload = {"text": "t", "messages": [{"role": "user", "content": "hi"}]}
        for _ in range(20):
            resp = client.post("/api/chat", json=payload)
            assert resp.status_code != 429
        assert client.post("/api/chat", json=payload).status_code == 429

        app.dependency_overrides[get_current_user] = lambda: MagicMock(id="rate-user-2")
        resp = client.post("/api/chat", json=payload)
        assert resp.status_code != 429

    def test_upload_limit_per_user(self, client):
        with (
            patch("routers.transcription.check_limit", new_callable=AsyncMock),
            patch("routers.transcription.track_usage", new_callable=AsyncMock),
            patch("routers.transcription._transcribe_upload", return_value={"text": "ok", "duration": 0}),
        ):
            for _ in range(10):
                resp = client.post(
                    "/api/transcribe", files={"file": ("t.wav", b"x", "audio/wav")}
                )
                assert resp.status_code == 200, resp.text
            blocked = client.post(
                "/api/transcribe", files={"file": ("t.wav", b"x", "audio/wav")}
            )
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) >= 1

    def test_v1_rate_limited_when_enabled(self, client, monkeypatch):
        monkeypatch.setattr("routers._helpers.V1_API_ENABLED", True)
        monkeypatch.setattr("routers._helpers.API_KEY", "key-" + "b" * 60)
        monkeypatch.setitem(
            rate_limit.DEFAULT_BUCKETS, "v1_transcription", BucketSpec(2, 0.001)
        )
        headers = {"Authorization": "Bearer key-" + "b" * 60}
        try:
            for _ in range(2):
                resp = client.post(
                    "/v1/audio/transcriptions",
                    files={"file": ("t.wav", b"x", "audio/wav")},
                    headers=headers,
                )
                assert resp.status_code != 429
            blocked = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("t.wav", b"x", "audio/wav")},
                headers=headers,
            )
            assert blocked.status_code == 429
        finally:
            monkeypatch.setattr("routers._helpers.V1_API_ENABLED", False)
            reset_all()
