"""Shared bounded upload ingestion for REST endpoints.

Single source of truth for the authenticated upload pipeline used by
``POST /api/transcribe``, ``POST /api/transcribe/microphone`` and
``POST /v1/audio/transcriptions``:

* filename/extension validation (allowlisted extensions, bounded length),
* streaming the decoded body into a mode-0600 temporary file while
  counting bytes — the transfer aborts as soon as the decoded size
  exceeds ``MAX_UPLOAD_SIZE_MB`` (checked before every chunk write, so
  at most ``limit`` bytes ever reach disk),
* guaranteed tempfile cleanup on success, error and cancellation,
* a raw (pre-multipart) request-body limiter middleware that counts the
  encoded byte stream — client ``Content-Length`` is only used as a
  cheap early reject, never trusted for enforcement,
* a startup sweep that removes only application-owned stale tempfiles
  (the ``ds_up_`` prefix created here and the ``ds_ws_`` prefix created
  by ``gigaam_transcriber.ws_protocol``), age-gated to avoid racing
  live requests.

The limit is read from ``MAX_UPLOAD_SIZE_MB`` at request time so tests
and deployments can tune it without module reloads. The default (1024)
is the single aligned value shared with docker-compose*.yaml, the
frontend uploader and the README.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
from fastapi.responses import JSONResponse

from routers._helpers import SUPPORTED_EXTENSIONS

logger = logging.getLogger("dialogscribe-api")

DEFAULT_MAX_UPLOAD_SIZE_MB = 1024

# Application-owned tempfile prefixes (startup sweep only ever touches these).
UPLOAD_TEMP_PREFIX = "ds_up_"
WS_TEMP_PREFIX = "ds_ws_"
STALE_TEMP_PREFIXES = (UPLOAD_TEMP_PREFIX, WS_TEMP_PREFIX)
STALE_TEMP_AGE_SECONDS = 24 * 60 * 60

_MAX_EXTENSION_LENGTH = 16
_CLIP_CHUNK_SIZE = 1024 * 1024

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


def max_upload_mb() -> int:
    """Current upload limit in MiB (``MAX_UPLOAD_SIZE_MB`` env, default 1024).

    A non-integer value falls back to the default. An integer <= 0 is
    fail-closed: kept as-is so every upload is rejected with 413 rather
    than silently widening the limit.
    """
    raw = os.getenv("MAX_UPLOAD_SIZE_MB", "")
    try:
        return int(raw)
    except ValueError:
        if raw.strip():
            logger.warning(
                "Invalid MAX_UPLOAD_SIZE_MB=%r; using default %d",
                raw.strip()[:32],
                DEFAULT_MAX_UPLOAD_SIZE_MB,
            )
        return DEFAULT_MAX_UPLOAD_SIZE_MB


def max_upload_bytes() -> int:
    return max_upload_mb() * 1024 * 1024


def _body_limit_detail(scope: dict) -> Any:
    from routers._helpers import _openai_error

    message = f"Request body too large. Maximum allowed is {max_upload_mb()}MB"
    if str(scope.get("path", "")).startswith("/v1/"):
        return _openai_error(message, "invalid_request_error", 413)
    return message


class BodySizeLimitMiddleware:
    """Pure-ASGI raw request-body limiter with a streaming byte counter.

    Wraps ``receive`` and counts every ``http.request`` body chunk; as soon
    as the encoded size exceeds ``max_upload_bytes() + overhead`` (the
    overhead budget covers multipart framing and form fields) the wrapper
    raises ``HTTPException(413)`` — FastAPI re-raises HTTPException from
    its body-parsing catch-all, so the 413 (not a generic 400) reaches the
    client and the endpoint never runs. A ``Content-Length`` larger than
    the cap short-circuits before any body is read, but the header is
    never trusted for allowance: chunked/lying requests are caught by the
    counter.
    """

    # Multipart framing + form fields allowance on top of the file limit.
    MULTIPART_OVERHEAD_BYTES = 4 * 1024 * 1024

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http" or scope.get("method", "").upper() not in _BODY_METHODS:
            await self.app(scope, receive, send)
            return

        limit = max_upload_bytes() + self.MULTIPART_OVERHEAD_BYTES

        declared = _content_length(scope)
        if declared is not None and declared > limit:
            response = JSONResponse({"detail": _body_limit_detail(scope)}, status_code=413)
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> dict:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    raise HTTPException(status_code=413, detail=_body_limit_detail(scope))
            return message

        await self.app(scope, limited_receive, send)


def _content_length(scope: dict) -> int | None:
    for name, value in scope.get("headers") or []:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def plain_error_detail(message: str, status: int) -> str:
    """Default error payload: FastAPI's plain ``detail`` string."""
    _ = status
    return message


def openai_error_detail(message: str, status: int) -> dict[str, Any]:
    """OpenAI-compatible error payload for the ``/v1`` surface."""
    return {"error": {"message": message, "type": "invalid_request_error", "code": status}}


def _safe_unlink(path: str | None) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Failed to remove upload temporary file")


def _spool_to_temp(
    file: UploadFile,
    file_ext: str,
    error_detail: Callable[[str, int], Any],
) -> str:
    limit = max_upload_bytes()
    tmp_path: str | None = None
    size = 0
    try:
        # NamedTemporaryFile creates the file with mode 0600 (mkstemp).
        with tempfile.NamedTemporaryFile(
            delete=False, prefix=UPLOAD_TEMP_PREFIX, suffix=file_ext
        ) as tmp_file:
            tmp_path = tmp_file.name
            while True:
                chunk = file.file.read(_CLIP_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=error_detail(
                            f"File too large. Maximum allowed is {max_upload_mb()}MB", 413
                        ),
                    )
                tmp_file.write(chunk)
    except HTTPException:
        _safe_unlink(tmp_path)
        raise
    except OSError as exc:
        _safe_unlink(tmp_path)
        logger.error(
            "Failed to spool upload to temporary storage (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=503,
            detail=error_detail("Temporary storage unavailable", 503),
        ) from exc

    if size == 0:
        _safe_unlink(tmp_path)
        raise HTTPException(
            status_code=400,
            detail=error_detail("Uploaded file is empty", 400),
        )
    return tmp_path


@contextlib.contextmanager
def spool_upload(
    file: UploadFile,
    *,
    error_detail: Callable[[str, int], Any] = plain_error_detail,
) -> Iterator[str]:
    """Validate and stream an upload to a 0600 tempfile; yield its path.

    The tempfile is always removed — on success, on any error raised by
    the caller's block, and on cancellation. Size is enforced while
    streaming (abort at limit+1 decoded byte), so at most ``limit``
    bytes are ever written to disk.
    """
    filename = file.filename or ""
    file_ext = Path(filename).suffix.lower()
    if len(file_ext) > _MAX_EXTENSION_LENGTH or file_ext not in SUPPORTED_EXTENSIONS:
        display = file_ext[:32] or "unknown"
        raise HTTPException(
            status_code=400,
            detail=error_detail(f"Unsupported file format: '{display}'", 400),
        )

    tmp_path = _spool_to_temp(file, file_ext, error_detail)
    try:
        yield tmp_path
    finally:
        _safe_unlink(tmp_path)


def sweep_stale_tempfiles(
    *,
    age_seconds: int = STALE_TEMP_AGE_SECONDS,
    prefixes: tuple[str, ...] = STALE_TEMP_PREFIXES,
    tmp_dir: str | None = None,
) -> int:
    """Delete application-owned tempfiles older than ``age_seconds``.

    Only files whose names start with one of ``prefixes`` inside the
    system temp directory are considered; everything else is untouched.
    Returns the removed count (callers log the count only).
    """
    root = tmp_dir or tempfile.gettempdir()
    cutoff = time.time() - age_seconds
    removed = 0
    try:
        entries = os.scandir(root)
    except OSError:
        return 0
    with entries:
        for entry in entries:
            try:
                if not entry.name.startswith(prefixes):
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                if entry.stat(follow_symlinks=False).st_mtime > cutoff:
                    continue
                os.unlink(entry.path)
                removed += 1
            except OSError:
                continue
    return removed
