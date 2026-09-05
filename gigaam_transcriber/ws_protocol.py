"""Shared WebSocket session protocol: first-frame auth + bounded binary streaming.

Used by both WS endpoints (``/api/autoflow/ws`` and ``/api/live-hints/ws``).

Protocol version 1
==================

Handshake
---------
1. The client connects WITHOUT credentials in the URL. Supplying ``?token=``
   is rejected with close code 4400 to prevent downgrade to query-string auth.
2. The server accepts the transport.
3. Within ``WS_AUTH_TIMEOUT_SECONDS`` (default 10 s) the client must send ONE
   TEXT frame of at most 64 KiB::

       {"type": "auth", "token": "<access JWT>", "protocol": 1}

4. The server validates the JWT (signature, expiry, ``type == "access"``) and
   performs a fresh DB lookup: the user must exist and be active.
   Authentication happens ONCE at connection acceptance — an accepted
   long-running session MAY finish after the token expires.
5. The server replies ``{"type": "auth_ok", "protocol": 1, "seq": 1}``; every
   server->client JSON message afterwards carries a strictly monotonic ``seq``.

Close codes
-----------
=====  ===============================================================
Code   Meaning
====== ===============================================================
4400   Bad protocol shape/version (incl. query-string token downgrade)
4401   Auth failure (missing/malformed frame, bad/expired token, unknown user)
4403   Authenticated user exists but is inactive
4408   Auth frame not received within the timeout
4413   Payload too large (frame, declared size, or cumulative cap)
====== ===============================================================

File upload mode (autoflow)
---------------------------
After ``auth_ok`` the client sends ONE TEXT header frame::

    {"type": "meta", "bytes": <int>, "filename": "...", ...arbitrary config}

``bytes`` must satisfy ``0 < bytes <= WS_MAX_TOTAL_BYTES``. The server
acknowledges with ``{"type": "ack", "meta_ok": true, "bytes": N, "seq": n}``
and then accepts BINARY frames only, appended to a mode-0600 tempfile with
prefix ``ds_ws_`` until exactly ``bytes`` have arrived. Per-frame size is
bounded by ``WS_MAX_FRAME_BYTES``; exceeding the declared size deletes the
tempfile and closes with 4413. Any TEXT frame during the upload closes with
4400. When the declared size is reached the router resumes its stage logic
and MUST finish with a terminal event.

Streaming mode (live hints)
---------------------------
Mixed TEXT control frames (``session_config``, ``brief_update``,
``hint_feedback``, ``hint_request``) and BINARY audio frames. A binary audio
frame is ``[source_tag: 1 byte (1=mic, 2=tab)][webm/opus bytes...]`` — the
tag makes every chunk self-describing. Per-frame and cumulative audio caps
use the same env knobs as upload mode; violations close with 4413.

Outbound ordering contract
--------------------------
All server->client messages go through :class:`OutboundChannel` (one bounded
queue + one sender task, monotonic ``seq``). A terminal event
(``type: "complete" | "error"``) is enqueued last and awaited before the
socket close. On disconnect/error the sender task is cancelled and joined;
send failures after disconnect are logged at DEBUG, never swallowed silently.

Env knobs (read per connection)
-------------------------------
=========================  ======================================
Variable                   Default
=========================  ======================================
WS_AUTH_TIMEOUT_SECONDS    10
WS_MAX_FRAME_BYTES         1048576
WS_MAX_TOTAL_BYTES         MAX_UPLOAD_SIZE_MB * 1 MiB (default 1024)
=========================  ======================================

Example
=======
>>> async def handler(ws: WebSocket):                       # doctest: +SKIP
...     async with ws_db_session() as db:
...         identity = await authenticate_websocket(ws, db)
...     if identity is None:
...         return
...     channel = OutboundChannel(ws)
...     await channel.start()
...     await channel.send({"type": "auth_ok", "protocol": 1})
...     try:
...         upload = BinaryUpload(ws, channel=channel)
...         meta = await upload.receive_meta()
...         if meta is None or not await upload.receive_binary():
...             return
...         await channel.close_terminal({"type": "complete"})
...     finally:
...         await channel.shutdown()
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gigaam_transcriber.auth import TokenValidationError, decode_access_token
from gigaam_transcriber.database import async_session_factory
from gigaam_transcriber.models import User

logger = logging.getLogger("dialogscribe-ws-protocol")

WS_PROTOCOL_VERSION = 1

CLOSE_BAD_PROTOCOL = 4400
CLOSE_AUTH_FAILED = 4401
CLOSE_INACTIVE_USER = 4403
CLOSE_AUTH_TIMEOUT = 4408
CLOSE_TOO_LARGE = 4413

SOURCE_TAGS = {1: "mic", 2: "tab"}
TAG_FOR_SOURCE = {"mic": 1, "tab": 2}

MAX_TEXT_FRAME_BYTES = 64 * 1024
DEFAULT_MAX_FRAME_BYTES = 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 1024 * 1024 * 1024

TERMINAL_TYPES = frozenset({"complete", "error"})


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class WsLimits:
    """Per-connection bounds; :meth:`from_env` is re-read on every connection."""

    auth_timeout_seconds: float = 10.0
    max_text_frame_bytes: int = MAX_TEXT_FRAME_BYTES
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES

    @classmethod
    def from_env(cls) -> WsLimits:
        upload_cap = _env_int("MAX_UPLOAD_SIZE_MB", 1024) * 1024 * 1024
        return cls(
            auth_timeout_seconds=_env_float("WS_AUTH_TIMEOUT_SECONDS", 10.0),
            max_text_frame_bytes=MAX_TEXT_FRAME_BYTES,
            max_frame_bytes=_env_int("WS_MAX_FRAME_BYTES", DEFAULT_MAX_FRAME_BYTES),
            max_total_bytes=_env_int("WS_MAX_TOTAL_BYTES", upload_cap),
        )


@dataclass(frozen=True)
class WsIdentity:
    """Plain authenticated-user values handed to workers (no ORM/session)."""

    user_id: str
    role: str


async def close_ws(ws: WebSocket, code: int, reason: str = "") -> None:
    try:
        await ws.close(code=code, reason=reason[:120])
    except Exception:
        logger.debug("websocket close(code=%s) failed", code, exc_info=True)


@contextlib.asynccontextmanager
async def ws_db_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


async def authenticate_websocket(
    ws: WebSocket, db: AsyncSession, *, limits: WsLimits | None = None
) -> WsIdentity | None:
    """Accept the socket and run the first-frame auth handshake.

    Returns the identity on success. On ANY failure (downgrade attempt,
    timeout, malformed frame, bad token, unknown/inactive user) the socket is
    closed with the documented 44xx code and ``None`` is returned — callers
    simply ``return``.
    """
    limits = limits or WsLimits.from_env()

    if "token" in ws.query_params:
        await ws.accept()
        await close_ws(ws, CLOSE_BAD_PROTOCOL, "token in query string is not allowed")
        return None

    await ws.accept()

    try:
        msg = await asyncio.wait_for(ws.receive(), timeout=limits.auth_timeout_seconds)
    except asyncio.TimeoutError:
        await close_ws(ws, CLOSE_AUTH_TIMEOUT, "auth frame timeout")
        return None
    except RuntimeError:
        return None

    if msg.get("type") != "websocket.receive":
        return None

    if "bytes" in msg:
        await close_ws(ws, CLOSE_AUTH_FAILED, "binary frame before auth")
        return None

    text = msg.get("text") or ""
    if len(text.encode("utf-8")) > limits.max_text_frame_bytes:
        await close_ws(ws, CLOSE_AUTH_FAILED, "auth frame too large")
        return None

    try:
        frame = json.loads(text)
    except ValueError:
        await close_ws(ws, CLOSE_AUTH_FAILED, "auth frame is not valid JSON")
        return None
    if not isinstance(frame, dict):
        await close_ws(ws, CLOSE_AUTH_FAILED, "auth frame must be a JSON object")
        return None
    if frame.get("type") != "auth":
        await close_ws(ws, CLOSE_BAD_PROTOCOL, "first frame must have type=auth")
        return None
    token = frame.get("token")
    if not isinstance(token, str) or not token:
        await close_ws(ws, CLOSE_AUTH_FAILED, "auth frame missing token")
        return None
    if frame.get("protocol") != WS_PROTOCOL_VERSION:
        await close_ws(ws, CLOSE_BAD_PROTOCOL, "unsupported protocol version")
        return None

    try:
        payload = decode_access_token(token)
    except TokenValidationError:
        await close_ws(ws, CLOSE_AUTH_FAILED, "invalid or expired token")
        return None

    user_id = payload["sub"]
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        await close_ws(ws, CLOSE_AUTH_FAILED, "unknown user")
        return None
    if not user.is_active:
        await close_ws(ws, CLOSE_INACTIVE_USER, "user is inactive")
        return None

    return WsIdentity(user_id=user_id, role=str(user.role or ""))


class OutboundChannel:
    """Single-writer outbound queue with monotonic ``seq`` and terminal ordering."""

    def __init__(self, ws: WebSocket, *, maxsize: int = 256) -> None:
        self._ws = ws
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=maxsize)
        self._task: asyncio.Task | None = None
        self._seq = 0

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def done(self) -> bool:
        return self._task is None or self._task.done()

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="ws-outbound-sender")

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            try:
                await self._ws.send_text(json.dumps(item, ensure_ascii=False))
            except Exception:
                logger.debug("outbound send failed (client gone?)", exc_info=True)
                return

    def emit(self, obj: dict) -> bool:
        """Non-blocking enqueue (drops the event when the queue is full)."""
        if self.done:
            return False
        self._seq += 1
        try:
            self._queue.put_nowait({**obj, "seq": self._seq})
            return True
        except asyncio.QueueFull:
            logger.debug("outbound queue full; dropped seq=%d", self._seq)
            return False

    async def send(self, obj: dict) -> bool:
        """Awaitable enqueue — waits when the bounded queue is full."""
        if self.done:
            return False
        self._seq += 1
        await self._queue.put({**obj, "seq": self._seq})
        return True

    async def close_terminal(self, obj: dict) -> None:
        """Enqueue the terminal event, flush it, then close the socket.

        The terminal message is guaranteed to be the LAST message sent on the
        connection. Idempotent: a no-op when the sender already stopped.
        """
        if self._task is not None and not self._task.done():
            self._seq += 1
            await self._queue.put({**obj, "seq": self._seq})
            await self._queue.put(None)
            try:
                await self._task
            except Exception:
                logger.debug("sender task raised during terminal flush", exc_info=True)
            self._task = None
            await close_ws(self._ws, 1000)
        else:
            self._task = None

    async def shutdown(self) -> None:
        """Cancel + join the sender task (disconnect/error paths). Idempotent."""
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._task = None


class WsProtocolError(Exception):
    """The receiver closed the socket after a protocol violation."""

    def __init__(self, code: int, reason: str = "") -> None:
        super().__init__(reason or f"websocket closed with {code}")
        self.code = code
        self.reason = reason


def _discard(path: str | None) -> None:
    if path:
        with contextlib.suppress(OSError):
            os.unlink(path)


class BinaryUpload:
    """File-upload receiver: one meta header frame, then binary frames to disk.

    Payload is streamed into a mode-0600 ``NamedTemporaryFile`` (prefix
    ``ds_ws_``). Size violations delete the tempfile and close with 4413;
    disconnects also remove the tempfile. After a successful
    :meth:`receive_binary` the caller owns ``path`` and must unlink it.
    """

    def __init__(
        self,
        ws: WebSocket,
        *,
        channel: OutboundChannel | None = None,
        limits: WsLimits | None = None,
        tmp_dir: str | None = None,
        suffix: str = ".upload",
    ) -> None:
        self._ws = ws
        self._channel = channel
        self._limits = limits or WsLimits.from_env()
        self._tmp_dir = tmp_dir
        self.suffix = suffix
        self.path: str | None = None
        self.meta: dict[str, Any] | None = None
        self.declared_bytes = 0
        self.received_bytes = 0

    async def receive_meta(self) -> dict[str, Any] | None:
        """Read and validate the meta frame; emit the ``ack`` on success."""
        try:
            msg = await self._ws.receive()
        except (WebSocketDisconnect, RuntimeError):
            return None
        if msg.get("type") != "websocket.receive":
            return None

        if "bytes" in msg:
            await close_ws(self._ws, CLOSE_BAD_PROTOCOL, "binary frame before meta")
            return None
        text = msg.get("text") or ""
        if len(text.encode("utf-8")) > self._limits.max_text_frame_bytes:
            await close_ws(self._ws, CLOSE_TOO_LARGE, "meta frame too large")
            return None
        try:
            meta = json.loads(text)
        except ValueError:
            await close_ws(self._ws, CLOSE_BAD_PROTOCOL, "meta frame is not valid JSON")
            return None
        if not isinstance(meta, dict) or meta.get("type") != "meta":
            await close_ws(self._ws, CLOSE_BAD_PROTOCOL, "expected type=meta")
            return None
        declared = meta.get("bytes")
        if not isinstance(declared, int) or isinstance(declared, bool) or declared <= 0:
            await close_ws(
                self._ws, CLOSE_BAD_PROTOCOL, "meta.bytes must be a positive integer"
            )
            return None
        if declared > self._limits.max_total_bytes:
            await close_ws(self._ws, CLOSE_TOO_LARGE, "declared size exceeds session cap")
            return None

        self.meta = meta
        self.declared_bytes = declared
        if self._channel is not None:
            self._channel.emit({"type": "ack", "meta_ok": True, "bytes": declared})
        return meta

    async def receive_binary(self) -> bool:
        """Append binary frames to the tempfile until ``declared_bytes`` arrive.

        Returns True on completion (tempfile left in place at ``path``).
        Returns False after closing the socket on any violation (tempfile
        deleted) or when the client disconnects mid-upload.
        """
        fh = tempfile.NamedTemporaryFile(
            prefix="ds_ws_",
            suffix=self.suffix,
            delete=False,
            dir=self._tmp_dir,
            mode="wb",
        )
        self.path = fh.name
        completed = False
        try:
            while self.received_bytes < self.declared_bytes:
                msg = await self._ws.receive()
                if msg.get("type") != "websocket.receive":
                    return False
                payload = msg.get("bytes")
                if payload is None:
                    await close_ws(
                        self._ws, CLOSE_BAD_PROTOCOL, "text frame during binary upload"
                    )
                    return False
                if len(payload) > self._limits.max_frame_bytes:
                    await close_ws(self._ws, CLOSE_TOO_LARGE, "frame exceeds per-frame limit")
                    return False
                fh.write(payload)
                self.received_bytes += len(payload)
                if self.received_bytes > self.declared_bytes:
                    await close_ws(
                        self._ws, CLOSE_TOO_LARGE, "received more than declared"
                    )
                    return False
            fh.flush()
            completed = True
            return True
        finally:
            fh.close()
            if not completed:
                _discard(self.path)
                self.path = None


class StreamSession:
    """Mixed TEXT-control + BINARY-audio receiver (live-hints mode).

    ``receive()`` returns ``("text", data_dict)`` or ``("audio", source, bytes)``
    where binary frames are ``[source_tag: 1 byte (1=mic, 2=tab)][webm bytes]``.
    Raises :class:`WebSocketDisconnect` when the client goes away and
    :class:`WsProtocolError` (socket already closed) on violations.
    """

    def __init__(self, ws: WebSocket, *, limits: WsLimits | None = None) -> None:
        self._ws = ws
        self._limits = limits or WsLimits.from_env()
        self.audio_bytes = 0

    async def receive(self) -> tuple[str, Any]:
        msg = await self._ws.receive()
        if msg.get("type") != "websocket.receive":
            raise WebSocketDisconnect(msg.get("code") or 1000)

        if "text" in msg:
            text = msg["text"]
            if len(text.encode("utf-8")) > self._limits.max_text_frame_bytes:
                await close_ws(self._ws, CLOSE_TOO_LARGE, "text frame too large")
                raise WsProtocolError(CLOSE_TOO_LARGE, "text frame too large")
            try:
                data = json.loads(text)
            except ValueError:
                await close_ws(self._ws, CLOSE_BAD_PROTOCOL, "text frame is not valid JSON")
                raise WsProtocolError(CLOSE_BAD_PROTOCOL, "text frame is not valid JSON") from None
            if not isinstance(data, dict):
                await close_ws(self._ws, CLOSE_BAD_PROTOCOL, "text frame must be a JSON object")
                raise WsProtocolError(CLOSE_BAD_PROTOCOL, "text frame must be a JSON object")
            return ("text", data)

        payload = msg.get("bytes") or b""
        if len(payload) < 1:
            await close_ws(self._ws, CLOSE_BAD_PROTOCOL, "empty binary frame")
            raise WsProtocolError(CLOSE_BAD_PROTOCOL, "empty binary frame")
        source = SOURCE_TAGS.get(payload[0])
        if source is None:
            await close_ws(self._ws, CLOSE_BAD_PROTOCOL, "unknown audio source tag")
            raise WsProtocolError(CLOSE_BAD_PROTOCOL, "unknown audio source tag")
        audio = payload[1:]
        if len(audio) > self._limits.max_frame_bytes:
            await close_ws(self._ws, CLOSE_TOO_LARGE, "audio frame exceeds per-frame limit")
            raise WsProtocolError(CLOSE_TOO_LARGE, "audio frame exceeds per-frame limit")
        self.audio_bytes += len(audio)
        if self.audio_bytes > self._limits.max_total_bytes:
            await close_ws(self._ws, CLOSE_TOO_LARGE, "cumulative audio cap exceeded")
            raise WsProtocolError(CLOSE_TOO_LARGE, "cumulative audio cap exceeded")
        return ("audio", source, audio)
