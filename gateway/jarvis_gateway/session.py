"""Per-device session state and the one-live-session registry (T013).

The plan's state machine (plan "State machine") is represented by
``SessionState``; the registry enforces "one session per device id, newest
wins" (FR-011, D11) and the idle reaper. This module owns mutable per-device
state and nothing else: no socket, no audio, no turn sequencing.
"""

from __future__ import annotations

import enum
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = ["DeviceSession", "SessionRegistry", "SessionState"]


class SessionState(enum.Enum):
    CONNECTED = enum.auto()
    IDLE = enum.auto()
    LISTENING = enum.auto()
    THINKING = enum.auto()
    SPEAKING = enum.auto()
    CANCELLING = enum.auto()
    CLOSED = enum.auto()


@dataclass(slots=True)
class DeviceSession:
    """One live robot connection."""

    device_id: str
    client_id: str
    protocol_version: int
    session_id: str
    state: SessionState = SessionState.CONNECTED
    tools: list[Any] = field(default_factory=list)
    pending_calls: dict[int, Any] = field(default_factory=dict)
    next_rpc_id: int = 1
    utterance: Any = None
    turn: Any = None
    ui: Any = None
    mcp_enabled: bool = False
    close_reason: str | None = None
    created_at: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if not isinstance(self.device_id, str) or not self.device_id:
            raise ValueError("device_id is required")
        if not isinstance(self.client_id, str) or not self.client_id:
            raise ValueError("client_id is required")
        if not isinstance(self.protocol_version, int) or self.protocol_version < 1:
            raise ValueError("protocol_version must be a positive integer")
        if not isinstance(self.session_id, str) or not self.session_id:
            raise ValueError("session_id is required")

    def touch(self, now: float | None = None) -> float:
        """Record activity; returns the timestamp used."""

        self.last_activity = time.monotonic() if now is None else now
        return self.last_activity


class SessionRegistry:
    """The one place that maps device ids to live sessions."""

    def __init__(
        self,
        *,
        idle_s: float = 120.0,
        on_replace: Callable[[DeviceSession], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if idle_s <= 0:
            raise ValueError("idle_s must be positive")
        self.idle_s = idle_s
        self._on_replace = on_replace
        self._clock = clock
        self._sessions: dict[str, DeviceSession] = {}

    def register(self, session: DeviceSession, now: float | None = None) -> DeviceSession | None:
        """Add ``session``, replacing any live session for the same device.

        Returns the replaced session (already closed) or ``None``. Re-registering
        the identical object is a no-op and returns ``None``.
        """

        now = self._clock() if now is None else now
        existing = self._sessions.get(session.device_id)
        if existing is session:
            return None
        session.last_activity = now
        self._sessions[session.device_id] = session
        if existing is None:
            return None
        self._close(existing, "replaced")
        if self._on_replace is not None:
            self._on_replace(existing)
        return existing

    def get(self, device_id: str) -> DeviceSession | None:
        return self._sessions.get(device_id)

    def close(self, session: DeviceSession, reason: str | None = None) -> None:
        if self._sessions.get(session.device_id) is session:
            del self._sessions[session.device_id]
        if session.state is not SessionState.CLOSED:
            self._close(session, reason or "closed")

    def scan_idle(self, now: float | None = None) -> list[DeviceSession]:
        """Close every session whose last activity is at least ``idle_s`` ago."""

        now = self._clock() if now is None else now
        expired = [s for s in self._sessions.values() if now - s.last_activity >= self.idle_s]
        for session in expired:
            self.close(session, "idle_timeout")
        return expired

    def counts(self) -> dict[str, Any]:
        by_state = {state.name.lower(): 0 for state in SessionState}
        for session in self._sessions.values():
            by_state[session.state.name.lower()] += 1
        return {"total": len(self._sessions), "by_state": by_state}

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, device_id: object) -> bool:
        return device_id in self._sessions

    def __iter__(self):
        return iter(self._sessions.values())

    @staticmethod
    def _close(session: DeviceSession, reason: str) -> None:
        session.state = SessionState.CLOSED
        session.close_reason = reason
        session.utterance = None
        session.turn = None
        session.ui = None
        session.tools = []
        session.pending_calls.clear()
