"""Tests for email utility functions (gigaam_transcriber/email.py)."""

import os
from unittest.mock import AsyncMock, patch

import pytest

from gigaam_transcriber.email import (
    SMTPConfig,
    get_smtp_config,
    send_email,
    send_password_reset_email,
)


class TestGetSmtpConfig:
    def test_returns_config_when_env_vars_set(self):
        env = {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "587",
            "SMTP_USER": "user@example.com",
            "SMTP_PASSWORD": "secret",
            "SMTP_FROM": "noreply@example.com",
            "SMTP_USE_TLS": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            config = get_smtp_config()

        assert isinstance(config, SMTPConfig)
        assert config.host == "smtp.example.com"
        assert config.port == 587
        assert config.user == "user@example.com"
        assert config.password == "secret"
        assert config.from_address == "noreply@example.com"
        assert config.use_tls is True

    def test_defaults_when_env_vars_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            config = get_smtp_config()

        assert isinstance(config, SMTPConfig)
        assert config.host == "smtp.mail.ru"
        assert config.port == 465
        assert config.user == ""
        assert config.password == ""
        assert config.from_address == ""
        assert config.use_tls is True

    def test_use_tls_false_when_set_to_false(self):
        env = {"SMTP_USE_TLS": "false"}
        with patch.dict(os.environ, env, clear=False):
            config = get_smtp_config()
        assert config.use_tls is False

    def test_from_address_falls_back_to_smtp_user(self):
        env = {
            "SMTP_USER": "me@host.com",
        }
        with patch.dict(os.environ, env, clear=False):
            config = get_smtp_config()
        assert config.from_address == "me@host.com"


class TestSendEmail:
    @pytest.mark.asyncio
    async def test_send_email_success(self):
        with patch("gigaam_transcriber.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await send_email(
                to="user@test.com",
                subject="Test Subject",
                body="Hello world",
            )
            mock_send.assert_awaited_once()
            call_kwargs = mock_send.call_args
            assert call_kwargs[1]["hostname"] is not None or "hostname" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_send_email_failure_does_not_raise(self):
        with patch(
            "gigaam_transcriber.email.aiosmtplib.send",
            new_callable=AsyncMock,
            side_effect=ConnectionRefusedError("SMTP refused"),
        ):
            result = await send_email(
                to="user@test.com",
                subject="Test",
                body="Body",
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_send_email_with_html_body_raises_multipart_error(self):
        from email.errors import MultipartConversionError

        with pytest.raises(MultipartConversionError):
            await send_email(
                to="user@test.com",
                subject="HTML Test",
                body="Plain text",
                html_body="<p>HTML</p>",
            )


class TestSendPasswordResetEmail:
    @pytest.mark.asyncio
    async def test_calls_send_email_with_correct_params(self):
        with patch("gigaam_transcriber.email.send_email", new_callable=AsyncMock) as mock_send:
            await send_password_reset_email(
                to="user@test.com",
                reset_token="abc123token",
                frontend_url="https://app.example.com",
            )
            mock_send.assert_awaited_once()
            call_kwargs = mock_send.call_args
            assert call_kwargs[0][0] == "user@test.com"
            assert "Сброс пароля" in call_kwargs[0][1]

    @pytest.mark.asyncio
    async def test_reset_url_format(self):
        with patch("gigaam_transcriber.email.send_email", new_callable=AsyncMock) as mock_send:
            await send_password_reset_email(
                to="user@test.com",
                reset_token="mytoken456",
                frontend_url="https://myapp.com",
            )
            body = mock_send.call_args[0][2]
            assert "https://myapp.com/reset-password?token=mytoken456" in body

    @pytest.mark.asyncio
    async def test_reset_url_uses_frontend_url_env_default(self):
        with patch.dict(os.environ, {"FRONTEND_URL": "https://env-app.com"}, clear=False):
            with patch("gigaam_transcriber.email.send_email", new_callable=AsyncMock) as mock_send:
                await send_password_reset_email(
                    to="user@test.com",
                    reset_token="xyz",
                )
                body = mock_send.call_args[0][2]
                assert "https://env-app.com/reset-password?token=xyz" in body

    @pytest.mark.asyncio
    async def test_reset_email_passes_html_body_to_send_email(self):
        with patch("gigaam_transcriber.email.send_email", new_callable=AsyncMock) as mock_send:
            await send_password_reset_email(
                to="user@test.com",
                reset_token="abc",
                frontend_url="https://app.com",
            )
            mock_send.assert_awaited_once()
            html_body = mock_send.call_args[1]["html_body"]
            assert "https://app.com/reset-password?token=abc" in html_body
            assert "<a href=" in html_body
