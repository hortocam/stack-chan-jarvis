"""aiohttp application shell: health, OTA stub, voice handshake and dispatch.

This is the only inbound listener the project adds (constitution I, plan D13).
The handshake is decided before the WebSocket is upgraded: a wrong or missing
token is an HTTP 401 and creates no session state (FR-002, SC-007).

After the hello, the dispatch loop owns the audio path: binary frames are Opus
at 16 kHz, 60 ms each, decoded and fed to the energy VAD; a completed utterance
(server VAD, the device's ``listen stop``, or the 30 s cap) becomes a turn.
Frames are only accepted in ``LISTENING``, so a late device packet cannot
corrupt a turn (plan "State machine").

The OTA handler here is the T015 stub with the shape plan C1 fixes; T031
replaces it with the config/body-driven manifest. It is deliberately not
exported as the final implementation.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import time
import uuid
from typing import Any

from aiohttp import WSMsgType, web

from . import __version__
from .ack import AckVoice
from .audio import UPLINK_RATE, AudioError, OpusDecoder
from .brain import BrainClient
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
from .stt import SttClient
from .tts import EdgeTts, TtsEngine
from .turn import TurnRunner
from .vad import Vad, frame_rms

__all__ = [
    "BRAIN",
    "CONFIG",
    "CONNECTIONS",
    "HELLO_TIMEOUT_S",
    "REGISTRY",
    "STT",
    "TTS",
    "create_app",
    "run",
]

logger = logging.getLogger(__name__)

# aiohttp 3.9+ asks for typed application keys; string keys emit NotAppKeyWarning.
CONFIG = web.AppKey("config", Config)
CLOCK = web.AppKey("clock", Any)
STARTED_AT = web.AppKey("started_at", float)
HELLO_TIMEOUT = web.AppKey("hello_timeout", float)
CONNECTIONS = web.AppKey("connections", dict)
REGISTRY = web.AppKey("registry", SessionRegistry)
STT = web.AppKey("stt", Any)
BRAIN = web.AppKey("brain", Any)
TTS = web.AppKey("tts", Any)
ACK = web.AppKey("ack", AckVoice)

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

    config: Config = request.app[CONFIG]
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


class _TurnRuntime:
    """Per-connection audio state: decoder, VAD, write lock and the turn task."""

    def __init__(self, config: Config) -> None:
        self.decoder = OpusDecoder(UPLINK_RATE)
        self.vad = Vad(
            silence_ms=config.vad_silence_ms,
            min_speech_ms=config.vad_min_speech_ms,
            max_utterance_s=config.max_utterance_s,
        )
        self.lock = asyncio.Lock()
        self.turn_task: asyncio.Task | None = None
        self.last_voiced_at: float | None = None

    def turn_running(self) -> bool:
        return self.turn_task is not None and not self.turn_task.done()


async def _dispatch(
    session: DeviceSession, ws: web.WebSocketResponse, *, config: Config, app: web.Application
) -> None:
    """The post-hello loop: audio in, VAD, turns out, device messages dispatched."""

    runtime = _TurnRuntime(config)

    async def send_json(payload: dict[str, Any]) -> None:
        async with runtime.lock:
            if not ws.closed:
                await ws.send_str(encode(payload))

    async def send_bytes(packet: bytes) -> None:
        async with runtime.lock:
            if not ws.closed:
                await ws.send_bytes(packet)

    async def run_turn(utterance, vad_end_ms: float) -> None:
        session.utterance = utterance
        session.state = SessionState.THINKING
        runner = TurnRunner(
            config=config,
            session_id=session.session_id,
            device_id=session.device_id,
            stt=app[STT],
            brain=app[BRAIN],
            tts=app[TTS],
            send_json=send_json,
            send_bytes=send_bytes,
            ack=app[ACK],
        )
        try:
            await runner.run(utterance, vad_end_ms=vad_end_ms)
        except asyncio.CancelledError:
            raise
        except Exception:  # never let one turn kill the session (FR-018)
            logger.exception("turn failed", extra={"session_id": session.session_id})
        finally:
            session.utterance = None
            session.turn = None
            if session.state is not SessionState.CLOSED:
                session.state = SessionState.IDLE

    def start_turn(utterance, ended_at: float) -> None:
        if runtime.turn_running():
            logger.warning(
                "utterance completed while a turn was running; dropping it",
                extra={"session_id": session.session_id},
            )
            return
        last_voiced = runtime.last_voiced_at if runtime.last_voiced_at is not None else ended_at
        vad_end_ms = max(0.0, (ended_at - last_voiced) * 1000.0)
        runtime.last_voiced_at = None
        session.state = SessionState.THINKING
        runtime.turn_task = asyncio.create_task(run_turn(utterance, vad_end_ms))
        session.turn = runtime.turn_task

    def offer(utterance, ended_at: float) -> None:
        if utterance is None:
            return
        if utterance.too_short or not utterance.pcm:
            logger.debug(
                "utterance discarded",
                extra={"reason": utterance.reason, "voiced_ms": utterance.voiced_ms},
            )
            return
        start_turn(utterance, ended_at)

    try:
        while not ws.closed:
            message = await ws.receive()
            if message.type in (
                WSMsgType.CLOSE,
                WSMsgType.CLOSING,
                WSMsgType.CLOSED,
                WSMsgType.ERROR,
            ):
                return
            session.touch()
            if message.type is WSMsgType.BINARY:
                if len(message.data) > MAX_MESSAGE_BYTES:
                    raise _ProtocolViolation(1008, "audio frame too large")
                # Invariant: audio is only accepted while listening. A frame that
                # arrives in THINKING/SPEAKING is dropped, never queued.
                if session.state is not SessionState.LISTENING:
                    continue
                try:
                    pcm = runtime.decoder.decode(message.data)
                except AudioError as exc:
                    logger.debug("dropping undecodable Opus frame: %s", exc)
                    continue
                now = asyncio.get_running_loop().time()
                if frame_rms(pcm) >= runtime.vad.config.continue_rms:
                    runtime.last_voiced_at = now
                utterance = runtime.vad.push(pcm)
                if utterance is not None:
                    offer(utterance, now)
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
                    if runtime.turn_running():
                        # Barge-in is US3 (T038); until then a new question waits.
                        logger.debug("listen start ignored while a turn is running")
                        continue
                    runtime.vad.reset()
                    runtime.last_voiced_at = None
                    session.state = SessionState.LISTENING
                elif parsed.state == "stop":
                    if session.state is not SessionState.LISTENING:
                        logger.debug("listen stop ignored: no utterance is open")
                        continue
                    now = asyncio.get_running_loop().time()
                    utterance = runtime.vad.finish("stop")
                    if utterance is None:
                        session.state = SessionState.IDLE
                    else:
                        offer(utterance, now)
                        if not runtime.turn_running():
                            session.state = SessionState.IDLE
                else:  # detect: the wake word; cancellation lands in T038
                    logger.debug("listen detect ignored until barge-in lands")
            elif isinstance(parsed, Abort):
                logger.debug("abort received with no cancellation handler yet")
            elif isinstance(parsed, Mcp):
                logger.debug("mcp payload received before the MCP client lands")
            elif isinstance(parsed, Hello):
                logger.debug("duplicate hello ignored")
    finally:
        if runtime.turn_task is not None:
            runtime.turn_task.cancel()
            with contextlib.suppress(BaseException):
                await runtime.turn_task
        runtime.decoder.close()


async def websocket_handler(request: web.Request) -> web.StreamResponse:
    refusal = _handshake_refusal(request)
    if refusal is not None:
        return refusal

    registry: SessionRegistry = request.app[REGISTRY]
    connections: dict[str, web.WebSocketResponse] = request.app[CONNECTIONS]
    device_id = request.headers["Device-Id"].strip()
    client_id = request.headers["Client-Id"].strip()

    ws = web.WebSocketResponse(max_msg_size=MAX_MESSAGE_BYTES)
    await ws.prepare(request)

    try:
        hello = await _read_hello(ws, request.app[HELLO_TIMEOUT])
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
        await _dispatch(session, ws, config=request.app[CONFIG], app=request.app)
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
    registry: SessionRegistry = request.app[REGISTRY]
    clock = request.app[CLOCK]
    return web.json_response(
        {
            "status": "ok",
            "version": __version__,
            "uptime_s": round(clock() - request.app[STARTED_AT], 3),
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

    config: Config = request.app[CONFIG]
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
    stt=None,
    brain=None,
    tts=None,
) -> web.Application:
    app = web.Application()
    app[CONFIG] = config
    app[CLOCK] = clock
    app[STARTED_AT] = clock()
    app[HELLO_TIMEOUT] = hello_timeout
    app[CONNECTIONS] = {}

    # One client per process: whisper and the brain keep HTTP connections warm.
    app[STT] = stt or SttClient(config.stt_url, timeout_s=config.stt_timeout_s)
    app[BRAIN] = brain or BrainClient(
        config.hermes_base_url,
        api_key=config.hermes_api_key,
        model=config.hermes_model,
        timeout_s=config.hermes_timeout_s,
    )
    app[TTS] = TtsEngine(
        tts if tts is not None else EdgeTts(config.voice, timeout_s=config.tts_timeout_s)
    )
    app[ACK] = AckVoice(
        config.ack_text, voice=config.voice, cache_dir=config.ack_cache_dir
    )

    async def _warm_ack(_app: web.Application) -> None:
        """Synthesise the one-off phrase before the first turn needs it."""

        try:
            await _app[ACK].frames(_app[TTS])
        except Exception:  # an unavailable phrase must never stop the gateway
            logger.exception("acknowledgement warm-up failed")

    app.on_startup.append(_warm_ack)

    async def _close_clients(_app: web.Application) -> None:
        for key in (STT, BRAIN):
            client = _app.get(key)
            close = getattr(client, "close", None)
            if close is not None:
                await close()

    app.on_cleanup.append(_close_clients)

    def close_replaced(old: DeviceSession) -> None:
        connection = app[CONNECTIONS].pop(old.session_id, None)
        if connection is not None and not connection.closed:
            asyncio.ensure_future(connection.close(code=1000, message=b"replaced"))

    if registry is None:
        registry = SessionRegistry(idle_s=config.session_idle_s, on_replace=close_replaced)
    app[REGISTRY] = registry

    app.router.add_get("/healthz", healthz)
    app.router.add_get(config.ota_path, ota_stub)
    app.router.add_post(config.ota_path, ota_stub)
    app.router.add_get(config.ws_path, websocket_handler)
    return app


def run(config: Config) -> None:
    web.run_app(create_app(config), host=config.bind_host, port=config.bind_port, print=None)
