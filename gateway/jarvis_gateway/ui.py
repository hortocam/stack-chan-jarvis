"""The ``self.ui.*`` tool surface: four best-effort calls over the voice socket.

The screen is a second consumer of the same MCP session the body tools use.
There is no second socket and no polling: one ``tools/call`` inside the
existing ``type: "mcp"`` frame per event (plan C3 shape, constitution II).

Everything here is deliberately small and strict. The four tool names and
their argument limits are the wire contract in ``docs/ui-design.md``; a tool
the gateway does not own is refused here rather than discovered on the
device, and every argument is range-checked before a byte leaves the gateway.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .jsonrpc import PendingRegistry, RpcClosed, RpcTimeout, id_allocator, request, wait_for
from .protocol import ProtocolError, mcp_message

__all__ = [
    "DEFAULT_HOLD_MS",
    "HEADLINE_MAX",
    "LABEL_MAX",
    "LOG_LINE_MAX",
    "UI_CLOCKS",
    "UI_CALL_TIMEOUT_S",
    "UI_GLOBE_FOCUS",
    "UI_LOG_LINE",
    "UI_STATE",
    "UI_STATES",
    "UI_TOOLS",
    "UiCallStats",
    "UiChannel",
    "UiError",
    "build_ui_call",
    "clocks_arguments",
]

logger = logging.getLogger(__name__)

UI_GLOBE_FOCUS = "self.ui.globe_focus"
UI_LOG_LINE = "self.ui.log_line"
UI_STATE = "self.ui.state"
UI_CLOCKS = "self.ui.clocks"

UI_TOOLS = frozenset({UI_GLOBE_FOCUS, UI_LOG_LINE, UI_STATE, UI_CLOCKS})
UI_STATES = frozenset(
    {"idle", "listening", "thinking", "speaking", "attention", "sleeping"}
)

#: A UI call is best-effort; after this deadline it is logged and dropped.
UI_CALL_TIMEOUT_S = 1.0

DEFAULT_HOLD_MS = 8000
MIN_HOLD_MS = 1000
MAX_HOLD_MS = 60_000

LABEL_MAX = 24
HEADLINE_MAX = 72
LOG_LINE_MAX = 120
TZ_MAX = 8

_CLOCK = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class UiError(ValueError):
    """A UI call that does not match the wire contract."""


def _name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UiError("name must be a non-empty string")
    return value


def _text(value: Any, field_name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise UiError(f"{field_name} must be a string")
    text = value.strip()
    if not text:
        raise UiError(f"{field_name} must not be empty")
    if len(text) > limit:
        raise UiError(f"{field_name} must be at most {limit} characters, got {len(text)}")
    return text


def _number(value: Any, field_name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UiError(f"{field_name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise UiError(f"{field_name} must be finite")
    if not low <= number <= high:
        raise UiError(f"{field_name} must be in {low:g}..{high:g}, got {number:g}")
    return number


def _hold_ms(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise UiError("hold_ms must be an integer")
    if not MIN_HOLD_MS <= value <= MAX_HOLD_MS:
        raise UiError(f"hold_ms must be in {MIN_HOLD_MS}..{MAX_HOLD_MS}, got {value}")
    return value


def _clock_time(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _CLOCK.match(value):
        raise UiError(f"{field_name} must be HH:MM in 24-hour time")
    return value


def _timezone(value: Any) -> str:
    if not isinstance(value, str):
        raise UiError("tz must be a string")
    tz = value.strip()
    if not tz or len(tz) > TZ_MAX:
        raise UiError(f"tz must be 1..{TZ_MAX} characters")
    if not all(character.isalnum() or character in "_-+/" for character in tz):
        raise UiError("tz must be a short zone label such as EST")
    return tz


def _check_keys(
    arguments: dict[str, Any], allowed: set[str], required: set[str] | None = None
) -> None:
    extra = set(arguments) - allowed
    if extra:
        raise UiError(f"unexpected argument(s): {', '.join(sorted(extra))}")
    missing = (required if required is not None else allowed) - set(arguments)
    if missing:
        raise UiError(f"missing argument(s): {', '.join(sorted(missing))}")


def _globe_focus(arguments: dict[str, Any]) -> dict[str, Any]:
    _check_keys(
        arguments,
        {"lat", "lon", "label", "headline", "hold_ms"},
        {"lat", "lon", "label", "headline"},
    )
    arguments = dict(arguments)
    hold_ms = _hold_ms(arguments.pop("hold_ms", DEFAULT_HOLD_MS))
    return {
        "lat": _number(arguments["lat"], "lat", -90.0, 90.0),
        "lon": _number(arguments["lon"], "lon", -180.0, 180.0),
        "label": _text(arguments["label"], "label", LABEL_MAX),
        "headline": _text(arguments["headline"], "headline", HEADLINE_MAX),
        "hold_ms": hold_ms,
    }


def _log_line(arguments: dict[str, Any]) -> dict[str, Any]:
    _check_keys(arguments, {"text"})
    return {"text": _text(arguments["text"], "text", LOG_LINE_MAX)}


def _state(arguments: dict[str, Any]) -> dict[str, Any]:
    _check_keys(arguments, {"name"})
    name = arguments["name"]
    if not isinstance(name, str) or name not in UI_STATES:
        raise UiError(f"name must be one of {', '.join(sorted(UI_STATES))}, got {name!r}")
    return {"name": name}


def _clocks(arguments: dict[str, Any]) -> dict[str, Any]:
    _check_keys(arguments, {"utc", "local", "tz"})
    return {
        "utc": _clock_time(arguments["utc"], "utc"),
        "local": _clock_time(arguments["local"], "local"),
        "tz": _timezone(arguments["tz"]),
    }


_VALIDATORS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    UI_GLOBE_FOCUS: _globe_focus,
    UI_LOG_LINE: _log_line,
    UI_STATE: _state,
    UI_CLOCKS: _clocks,
}


def build_ui_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate one UI tool call; return ``{"name", "arguments"}``.

    Raises :class:`UiError` naming the offending tool or field. The error is
    the negative control: an unknown tool or a malformed argument must not
    reach the device.
    """

    validator = _VALIDATORS.get(name)
    if validator is None:
        raise UiError(f"unknown UI tool: {name}")
    if not isinstance(arguments, dict):
        raise UiError("arguments must be an object")
    return {"name": name, "arguments": validator(arguments)}


def clocks_arguments(now: float | None = None) -> dict[str, str]:
    """Header values for one instant: UTC and local ``HH:MM`` plus a zone label."""

    instant = time.time() if now is None else now
    local = time.localtime(instant)
    return {
        "utc": time.strftime("%H:%M", time.gmtime(instant)),
        "local": time.strftime("%H:%M", local),
        "tz": time.strftime("%Z", local) or "UTC",
    }


@dataclass(slots=True)
class UiCallStats:
    sent: int = 0
    ok: int = 0
    errors: int = 0
    timeouts: int = 0
    dropped: int = 0


class UiChannel:
    """One session's best-effort UI calls, with their own JSON-RPC ids.

    ``send`` is the session's text-frame sender (the one the voice path
    already holds a lock around). The channel never raises into a caller that
    published an event: a timeout, a device error or a closed socket is a
    counter and a debug line.
    """

    def __init__(
        self,
        send: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        session_id: str,
        timeout_s: float = UI_CALL_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
        next_id: Callable[[], int] | None = None,
    ) -> None:
        if not session_id:
            raise ValueError("session_id is required")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self._send = send
        self._session_id = session_id
        self._timeout_s = timeout_s
        self._clock = clock
        self._next_id = next_id if next_id is not None else id_allocator().next
        self._pending = PendingRegistry()
        self._stats = UiCallStats()
        self._closed: str | None = None

    @property
    def closed(self) -> bool:
        return self._closed is not None

    @property
    def stats(self) -> UiCallStats:
        return self._stats

    async def call(self, name: str, arguments: dict[str, Any]) -> bool:
        """Send one UI call and wait briefly for its answer.

        Returns ``True`` only when the device answered without an MCP error.
        Invalid calls raise :class:`UiError`; those are programming errors and
        never reach the wire.
        """

        call = build_ui_call(name, arguments)
        if self._closed is not None:
            self._stats.dropped += 1
            logger.debug("UI call %s dropped: %s", name, self._closed)
            return False

        rpc_id = self._next_id()
        self._pending.register(rpc_id)
        message = mcp_message(
            self._session_id,
            request(
                rpc_id,
                "tools/call",
                {"name": call["name"], "arguments": call["arguments"]},
            ),
        )
        try:
            await self._send(message)
        except (ProtocolError, ConnectionError, OSError, RuntimeError) as exc:
            self._pending.discard(rpc_id)
            self._stats.dropped += 1
            logger.debug("UI call %s dropped: send failed: %s", name, exc)
            return False

        self._stats.sent += 1
        try:
            payload = await wait_for(self._pending, rpc_id, self._timeout_s)
        except RpcTimeout:
            self._stats.timeouts += 1
            logger.debug("UI call %s timed out after %.2fs; dropped", name, self._timeout_s)
            return False
        except RpcClosed:
            self._stats.dropped += 1
            logger.debug("UI call %s dropped: session closed", name)
            return False
        except asyncio.CancelledError:
            self._pending.discard(rpc_id)
            raise

        error = payload.get("error")
        if isinstance(error, dict):
            self._stats.errors += 1
            logger.warning(
                "UI call %s failed: %s (code %s)",
                name,
                error.get("message", "device error"),
                error.get("code", "?"),
            )
            return False
        result = payload.get("result")
        if not isinstance(result, dict) or result.get("isError"):
            self._stats.errors += 1
            logger.warning("UI call %s returned an error result", name)
            return False

        self._stats.ok += 1
        return True

    def handle_payload(self, payload: dict[str, Any]) -> bool:
        """Resolve a device response; ``False`` when no UI call is waiting for it."""

        rpc_id = payload.get("id")
        if isinstance(rpc_id, bool) or not isinstance(rpc_id, int):
            return False
        return self._pending.resolve(rpc_id, payload)

    def close(self, reason: str = "closed") -> None:
        if self._closed is not None:
            return
        self._closed = reason
        self._pending.fail_all(RpcClosed(reason))
