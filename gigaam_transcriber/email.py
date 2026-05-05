"""Async email utility via aiosmtplib."""

import logging
import os
from dataclasses import dataclass
from email.mime.text import MIMEText

import aiosmtplib

logger = logging.getLogger(__name__)


@dataclass
class SMTPConfig:
    host: str
    port: int
    user: str
    password: str
    from_address: str
    use_tls: bool


def get_smtp_config() -> SMTPConfig:
    """Load SMTP config from environment variables."""
    return SMTPConfig(
        host=os.getenv("SMTP_HOST", "smtp.mail.ru"),
        port=int(os.getenv("SMTP_PORT", "465")),
        user=os.getenv("SMTP_USER", ""),
        password=os.getenv("SMTP_PASSWORD", ""),
        from_address=os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "")),
        use_tls=os.getenv("SMTP_USE_TLS", "true").lower() in ("true", "1", "yes"),
    )


async def send_email(to: str, subject: str, body: str, html_body: str | None = None) -> None:
    """Send an email using configured SMTP."""
    config = get_smtp_config()
    msg = MIMEText(body, "plain", "utf-8")
    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))
    msg["From"] = config.from_address
    msg["To"] = to
    msg["Subject"] = subject

    try:
        await aiosmtplib.send(
            msg,
            hostname=config.host,
            port=config.port,
            username=config.user,
            password=config.password,
            use_tls=config.use_tls,
        )
        logger.info("Email sent to %s: %s", to, subject)
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to, e)
        # Don't raise — we don't want email failures to crash the API


async def send_password_reset_email(to: str, reset_token: str, frontend_url: str | None = None) -> None:
    """Send password reset email with reset link."""
    base_url = frontend_url or os.getenv("FRONTEND_URL", "http://localhost:5173")
    reset_link = f"{base_url}/reset-password?token={reset_token}"

    subject = "DialogScribe — Сброс пароля"
    body = (
        f"Здравствуйте!\n\n"
        f"Вы запросили сброс пароля для вашего аккаунта в DialogScribe.\n\n"
        f"Перейдите по ссылке для установки нового пароля:\n"
        f"{reset_link}\n\n"
        f"Ссылка действительна 1 час.\n\n"
        f"Если вы не запрашивали сброс пароля, проигнорируйте это письмо.\n\n"
        f"С уважением,\nКоманда DialogScribe"
    )
    html_body = (
        f"<p>Здравствуйте!</p>"
        f"<p>Вы запросили сброс пароля для вашего аккаунта в DialogScribe.</p>"
        f"<p><a href=\"{reset_link}\">Сбросить пароль</a></p>"
        f"<p>Ссылка действительна 1 час.</p>"
        f"<p><small>Если вы не запрашивали сброс пароля, проигнорируйте это письмо.</small></p>"
        f"<p>С уважением,<br>Команда DialogScribe</p>"
    )

    await send_email(to, subject, body, html_body=html_body)
