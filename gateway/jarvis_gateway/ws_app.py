"""aiohttp application shell: health, OTA stub, voice handshake and dispatch (T015).

This is the only inbound listener the project adds (constitution I, plan D13).
The handshake is decided before the WebSocket is upgraded: a wrong or missing
token is an HTTP 401 and creates no session state (FR-002, SC-007).

The OTA handler here is the T015 stub with the shape plan C1 fixes; T031
replaces it with the config/body-driven manifest. It is deliberately not
exported as the final implementation.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from typing import Any

from aiohttp import WSMsgType, web

from . import __version__
from .config import Config
from .protocol import (
    Abort,
    Hello,
    Listen,
    Mcp,
    ProtocolError,
    encode,
    parse_device_message,
    server_hello,
)
from .session import DeviceSession, SessionRegistry, SessionState

__all__ = ["HELLO_TIMEOUT_S", "create_app", "run"]

logger = logging.getLogger(__name__)

HELLO_TIMEOUT_S = 10.0
MAX_MESSAGE_BYTES = 64 * 1024
PROTOCOL_VERSION = "1"

_WEBSOCKET_SCHEMES = {"http": "ws", "https": "wss"}


def _bearer(headers: Any) -> str | None:
    value = headers.get("Authorization")
    if not value:
        return None
    scheme, _, token = value.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip()


def _token_matches(expected: str, provided: str | None) -> bool:
    if not provided:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


def _handshake_refusal(request: web.Request) -> web.Response | None:
    """Return the pre-upgrade refusal, or ``None`` when the handshake may proceed."""

    config: Config = request.app["config"]
    if not _token_matches(config.device_token, _bearer(request.headers)):
        return web.Response(status=401, text="unauthorized")

    version = request.headers.get("Protocol-Version")
    if not version:
        return web.Response(status=400, text="Protocol-Version is required")
    if version != PROTOCOL_VERSION:
        return web.Response(status=426, text="binary protocol version 1 is required")

    if not (request.headers.get("Device-Id") or "").strip():
        return web.Response(status=400, text="Device-Id is required")
    if not (request.headers.get("Client-Id") or "").strip():
        return web.Response(status=400, text="Client-Id is required")
    return None


class _ProtocolViolation(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


async def _read_hello(ws: web.WebSocketResponse, limit_s: float) -> Hello | None:
    """Wait for the device hello; ``None`` means the timeout expired."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit_s
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return None
        try:
            message = await asyncio.wait_for(ws.receive(), timeout=remaining)
        except TimeoutError:
            return None
        if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR):
            raise _ProtocolViolation(1000, "closed before hello")
        if message.type is WSMsgType.BINARY:
            raise _ProtocolViolation(1003, "binary frame before hello")
        if message.type is not WSMsgType.TEXT:
            continue
        if len(message.data) > MAX_MESSAGE_BYTES:
            raise _ProtocolViolation(1008, "message too large")
        try:
            parsed = parse_device_message(message.data)
        except ProtocolError as exc:
            raise _ProtocolViolation(1003, str(exc)) from exc
        if isinstance(parsed, Hello):
            return parsed
        raise _ProtocolViolation(1008, "hello must be the first message")


async def _dispatch(session: DeviceSession, ws: web.WebSocketResponse) -> None:
    """The post-hello loop. User-story handlers attach here in later tasks."""

    while not ws.closed:
        message = await ws.receive()
        if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR):
            return
        session.touch()
        if message.type is WSMsgType.BINARY:
            if len(message.data) > MAX_MESSAGE_BYTES:
                raise _ProtocolViolation(1008, "audio frame too large")
            # The audio path lands with US1 (T020+); frames are dropped until then.
            continue
        if message.type is not WSMsgType.TEXT:
            continue
        if len(message.data) > MAX_MESSAGE_BYTES:
            raise _ProtocolViolation(1008, "message too large")
        try:
            parsed = parse_device_message(message.data)
        except ProtocolError as exc:
            raise _ProtocolViolation(1003, str(exc)) from exc
        if parsed is None:
            continue
        if isinstance(parsed, Listen):
            if parsed.state == "start":
                session.state = SessionState.LISTENING
            elif session.state is SessionState.LISTENING:
                session.state = SessionState.IDLE
        elif isinstance(parsed, Abort):
            logger.debug("abort received with no turn in flight")
        elif isinstance(parsed, Mcp):
            logger.debug("mcp payload received before the MCP client lands")
        elif isinstance(parsed, Hello):
            logger.debug("duplicate hello ignored")


async def websocket_handler(request: web.Request) -> web.StreamResponse:
    refusal = _handshake_refusal(request)
    if refusal is not None:
        return refusal

    registry: SessionRegistry = request.app["registry"]
    connections: dict[str, web.WebSocketResponse] = request.app["connections"]
    device_id = request.headers["Device-Id"].strip()
    client_id = request.headers["Client-Id"].strip()

    ws = web.WebSocketResponse(max_msg_size=MAX_MESSAGE_BYTES)
    await ws.prepare(request)

    try:
        hello = await _read_hello(ws, request.app["hello_timeout"])
    except _ProtocolViolation as exc:
        if exc.code != 1000:
            await ws.close(code=exc.code, message=exc.message.encode()[:120])
        return ws

    if hello is None:
        await ws.close(code=1008, message=b"hello timeout")
        return ws

    session = DeviceSession(
        device_id=device_id,
        client_id=client_id,
        protocol_version=int(request.headers["Protocol-Version"]),
        session_id=uuid.uuid4().hex,
        state=SessionState.IDLE,
    )
    connections[session.session_id] = ws
    registry.register(session)
    await ws.send_str(encode(server_hello(session.session_id)))
    logger.info(
        "session opened",
        extra={"device_id": device_id, "session_id": session.session_id, "mcp": bool(
            hello.features and hello.features.get("mcp")
        )},
    )

    violation: _ProtocolViolation | None = None
    try:
        await _dispatch(session, ws)
    except _ProtocolViolation as exc:
        violation = exc
    finally:
        connections.pop(session.session_id, None)
        registry.close(session, "protocol_error" if violation else "disconnected")
        if not ws.closed:
            code = violation.code if violation is not None else 1000
            message = violation.message.encode()[:120] if violation is not None else b""
            await ws.close(code=code, message=message)
        logger.info(
            "session closed",
            extra={"device_id": device_id, "session_id": session.session_id},
        )
    return ws


async def healthz(request: web.Request) -> web.Response:
    registry: SessionRegistry = request.app["registry"]
    clock = request.app["clock"]
    return web.json_response(
        {
            "status": "ok",
            "version": __version__,
            "uptime_s": round(clock() - request.app["started_at"], 3),
            "sessions": len(registry),
        }
    )


def _timezone_offset_minutes(now: float | None = None) -> int:
    """Minutes east of UTC, as the firmware expects (plan D15)."""

    local = time.localtime(time.time() if now is None else now)
    seconds_west = time.altzone if local.tm_isdst and time.daylight else time.timezone
    return -int(seconds_west // 60)


def _websocket_url(config: Config) -> str:
    scheme, _, rest = config.public_base_url.partition("://")
    return f"{_WEBSOCKET_SCHEMES.get(scheme, scheme)}://{rest.rstrip('/')}{config.ws_path}"


async def ota_stub(request: web.Request) -> web.Response:
    """T015's fixed manifest; T031 replaces this handler with ``ota.py``'s."""

    config: Config = request.app["config"]
    version = config.firmware_version
    if request.method == "POST":
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            body = None
        if isinstance(body, dict):
            application = body.get("application")
            if isinstance(application, dict) and isinstance(application.get("version"), str):
                version = application["version"] or version
    return web.json_response(
        {
            "firmware": {"version": version, "url": ""},
            "websocket": {
                "url": _websocket_url(config),
                "token": config.device_token,
                "version": 1,
            },
            "server_time": {
                "timestamp": int(time.time() * 1000),
                "timezone_offset": _timezone_offset_minutes(),
            },
        }
    )


def create_app(
    config: Config,
    *,
    registry: SessionRegistry | None = None,
    hello_timeout: float = HELLO_TIMEOUT_S,
    clock=time.monotonic,
) -> web.Application:
    app = web.Application()
    app["config"] = config
    app["clock"] = clock
    app["started_at"] = clock()
    app["hello_timeout"] = hello_timeout
    app["connections"] = {}

    def close_replaced(old: DeviceSession) -> None:
        connection = app["connections"].pop(old.session_id, None)
        if connection is not None and not connection.closed:
            asyncio.ensure_future(connection.close(code=1000, message=b"replaced"))

    if registry is None:
        registry = SessionRegistry(idle_s=config.session_idle_s, on_replace=close_replaced)
    app["registry"] = registry

    app.router.add_get("/healthz", healthz)
    app.router.add_get(config.ota_path, ota_stub)
    app.router.add_post(config.ota_path, ota_stub)
    app.router.add_get(config.ws_path, websocket_handler)
    return app


def run(config: Config) -> None:
    web.run_app(create_app(config), host=config.bind_host, port=config.bind_port, print=None)
