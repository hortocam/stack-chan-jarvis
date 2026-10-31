"""The xiaozhi v2.2.4 wire protocol, in one place (T007).

Only the message types the pinned firmware documents are represented here:
device→server ``hello``/``listen``/``abort``/``mcp`` and server→device
``hello``/``stt``/``llm``/``tts``/``mcp``. Each shape is a T002 fixture
transcribed from ``.research_stackchan/xiaozhi-esp32`` at tag ``v2.2.4``
(``docs/websocket.md`` §4, ``docs/mcp-protocol.md``), so a field that does not
appear there does not appear here either.

Two rules are enforced rather than trusted:

* a text frame that is not a JSON object, or that has no string ``type``, is a
  ``ProtocolError`` — the socket layer turns that into a 1003 close;
* an unknown ``type`` is ignored with one debug line, which is what the
  firmware does with messages it does not know (spec "Edge Cases").

Every outgoing message carries the session id issued in the server ``hello``;
the firmware echoes it, and a message without one could be applied to the
wrong session.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

__all__ = [
    "Abort",
    "AudioParams",
    "DeviceMessage",
    "Hello",
    "Listen",
    "Mcp",
    "ProtocolError",
    "DOWNLINK_AUDIO_PARAMS",
    "UPLINK_AUDIO_PARAMS",
    "encode",
    "llm_message",
    "mcp_message",
    "parse_device_message",
    "server_hello",
    "stt_message",
    "tts_sentence_start",
    "tts_start",
    "tts_stop",
]

logger = logging.getLogger(__name__)

LISTEN_STATES = frozenset({"start", "stop", "detect"})


class ProtocolError(ValueError):
    """A frame that violates the wire contract."""


@dataclass(frozen=True, slots=True)
class AudioParams:
    """The ``audio_params`` block of a hello, in both directions."""

    format: str = "opus"
    sample_rate: int = 16000
    channels: int = 1
    frame_duration: int = 60

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "frame_duration": self.frame_duration,
        }

    @classmethod
    def from_dict(cls, raw: Any, *, field_name: str = "audio_params") -> AudioParams:
        if not isinstance(raw, dict):
            raise ProtocolError(f"{field_name} must be a JSON object")
        try:
            params = cls(
                format=_string(raw.get("format", "opus"), field_name + ".format"),
                sample_rate=_integer(raw.get("sample_rate", 16000), field_name + ".sample_rate"),
                channels=_integer(raw.get("channels", 1), field_name + ".channels"),
                frame_duration=_integer(
                    raw.get("frame_duration", 60), field_name + ".frame_duration"
                ),
            )
        except ProtocolError:
            raise
        if params.sample_rate <= 0 or params.channels <= 0 or params.frame_duration <= 0:
            raise ProtocolError(f"{field_name} values must be positive")
        return params


UPLINK_AUDIO_PARAMS = AudioParams("opus", 16000, 1, 60)
DOWNLINK_AUDIO_PARAMS = AudioParams("opus", 24000, 1, 60)


def _string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"{field_name} must be a non-empty string")
    return value


def _integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{field_name} must be an integer")
    return value


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _string(value, field_name)


@dataclass(frozen=True, slots=True)
class Hello:
    version: int
    features: dict[str, Any] | None
    transport: str
    audio_params: AudioParams | None
    session_id: str | None = None
    type: str = "hello"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "version": self.version}
        if self.features is not None:
            out["features"] = self.features
        out["transport"] = self.transport
        if self.audio_params is not None:
            out["audio_params"] = self.audio_params.to_dict()
        if self.session_id is not None:
            out["session_id"] = self.session_id
        return out


@dataclass(frozen=True, slots=True)
class Listen:
    state: str
    mode: str | None = None
    text: str | None = None
    session_id: str | None = None
    type: str = "listen"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "state": self.state}
        if self.mode is not None:
            out["mode"] = self.mode
        if self.text is not None:
            out["text"] = self.text
        if self.session_id is not None:
            out["session_id"] = self.session_id
        return out


@dataclass(frozen=True, slots=True)
class Abort:
    reason: str | None = None
    session_id: str | None = None
    type: str = "abort"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type}
        if self.reason is not None:
            out["reason"] = self.reason
        if self.session_id is not None:
            out["session_id"] = self.session_id
        return out


@dataclass(frozen=True, slots=True)
class Mcp:
    payload: dict[str, Any]
    session_id: str | None = None
    type: str = "mcp"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "payload": self.payload}
        if self.session_id is not None:
            out["session_id"] = self.session_id
        return out


DeviceMessage = Hello | Listen | Abort | Mcp


def parse_device_message(text: str | bytes | bytearray) -> DeviceMessage | None:
    """Parse one device text frame.

    Returns ``None`` for a well-formed object with a type we do not implement
    (firmware parity: ignore it). Raises ``ProtocolError`` for anything that is
    not a JSON object with a usable ``type``.
    """

    if isinstance(text, (bytes, bytearray)):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("text frame is not valid UTF-8") from exc
    try:
        raw = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"malformed JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProtocolError("message must be a JSON object")

    kind = raw.get("type")
    if not isinstance(kind, str) or not kind:
        raise ProtocolError("message is missing a string 'type'")

    session_id = _optional_string(raw.get("session_id"), "session_id")

    if kind == "hello":
        audio_params = raw.get("audio_params")
        features = raw.get("features")
        if features is not None and not isinstance(features, dict):
            raise ProtocolError("hello.features must be a JSON object")
        return Hello(
            version=_integer(raw.get("version", 1), "hello.version"),
            features=features,
            transport=_string(raw.get("transport", "websocket"), "hello.transport"),
            audio_params=(
                AudioParams.from_dict(audio_params) if audio_params is not None else None
            ),
            session_id=session_id,
        )

    if kind == "listen":
        state = raw.get("state")
        if state not in LISTEN_STATES:
            raise ProtocolError(f"listen.state must be one of {sorted(LISTEN_STATES)}")
        return Listen(
            state=state,
            mode=_optional_string(raw.get("mode"), "listen.mode"),
            text=_optional_string(raw.get("text"), "listen.text"),
            session_id=session_id,
        )

    if kind == "abort":
        return Abort(
            reason=_optional_string(raw.get("reason"), "abort.reason"),
            session_id=session_id,
        )

    if kind == "mcp":
        payload = raw.get("payload")
        if not isinstance(payload, dict):
            raise ProtocolError("mcp.payload must be a JSON object")
        return Mcp(payload=payload, session_id=session_id)

    logger.debug("ignoring unknown message type %r", kind)
    return None


# --- outgoing messages -----------------------------------------------------


def _session_id(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ProtocolError("outgoing messages require a non-empty session_id")
    return value


def server_hello(session_id: str) -> dict[str, Any]:
    """The reply to the device's hello (plan C2, fixture ``hello_server.json``)."""

    return {
        "type": "hello",
        "transport": "websocket",
        "session_id": _session_id(session_id),
        "audio_params": DOWNLINK_AUDIO_PARAMS.to_dict(),
    }


def stt_message(session_id: str, text: str) -> dict[str, Any]:
    return {"session_id": _session_id(session_id), "type": "stt", "text": text}


def llm_message(session_id: str, emotion: str, text: str) -> dict[str, Any]:
    return {
        "session_id": _session_id(session_id),
        "type": "llm",
        "emotion": emotion,
        "text": text,
    }


def tts_start(session_id: str) -> dict[str, Any]:
    return {"session_id": _session_id(session_id), "type": "tts", "state": "start"}


def tts_sentence_start(session_id: str, text: str) -> dict[str, Any]:
    return {
        "session_id": _session_id(session_id),
        "type": "tts",
        "state": "sentence_start",
        "text": text,
    }


def tts_stop(session_id: str) -> dict[str, Any]:
    return {"session_id": _session_id(session_id), "type": "tts", "state": "stop"}


def mcp_message(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": _session_id(session_id),
        "type": "mcp",
        "payload": payload,
    }


def encode(message: dict[str, Any]) -> str:
    """Serialise one outgoing frame. Compact and UTF-8, like the firmware."""

    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))
