"""Per-request correlation IDs for error responses and server logs.

The middleware assigns a uuid4 hex per HTTP/WS connection (contextvar), echoes
it on the X-Correlation-ID response header, and a logging filter stamps the
active correlation id onto every log record so handlers/formatters can emit
`%(correlation_id)s`. Public error payloads built via `api_error` expose only
a stable code, a human message and the correlation id — never exception text.
"""

import logging
import uuid
from contextvars import ContextVar

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")

RESPONSE_HEADER = "X-Correlation-ID"


def get_correlation_id() -> str:
    return _correlation_id.get()


class CorrelationIdMiddleware:
    """Pure-ASGI middleware: assign correlation id, echo it on HTTP responses."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        token = _correlation_id.set(uuid.uuid4().hex)
        try:
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return

            async def send_with_correlation(message):
                if message["type"] == "http.response.start":
                    headers = message.setdefault("headers", [])
                    headers.append(
                        (RESPONSE_HEADER.lower().encode("latin-1"), get_correlation_id().encode("latin-1"))
                    )
                await send(message)

            await self.app(scope, receive, send_with_correlation)
        finally:
            _correlation_id.reset(token)


class CorrelationLogFilter(logging.Filter):
    """Stamp the active correlation id onto every record passing the handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = get_correlation_id()
        return True


def install_correlation_logging() -> None:
    """Attach the correlation filter to every current root-logger handler."""
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, CorrelationLogFilter) for f in handler.filters):
            handler.addFilter(CorrelationLogFilter())
