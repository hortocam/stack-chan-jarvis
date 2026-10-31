"""Contract tests for the wire protocol (T007).

Every message shape here is a T002 fixture, transcribed from xiaozhi-esp32 at
tag v2.2.4; nothing in these tests is invented. A round trip means: parse the
fixture, serialise it again, and get the same JSON object back. For server
messages the builder is the only source of truth, so it must reproduce the
fixture byte-for-byte at the object level.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from jarvis_gateway import protocol
from jarvis_gateway.protocol import ProtocolError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "protocol"

DEVICE_FIXTURES = [
    "hello_device.json",
    "listen_start.json",
    "listen_stop.json",
    "listen_detect.json",
    "abort.json",
    "mcp_initialize_result.json",
    "mcp_tools_list_result.json",
    "mcp_tools_call_result.json",
]


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", DEVICE_FIXTURES)
def test_device_fixture_round_trips(name: str) -> None:
    raw = load(name)

    message = protocol.parse_device_message(json.dumps(raw))

    assert message is not None
    assert message.to_dict() == raw


def test_parse_accepts_bytes() -> None:
    raw = load("abort.json")

    message = protocol.parse_device_message(json.dumps(raw).encode())

    assert message.to_dict() == raw


def test_hello_exposes_the_fields_the_dispatcher_needs() -> None:
    message = protocol.parse_device_message(json.dumps(load("hello_device.json")))

    assert isinstance(message, protocol.Hello)
    assert message.version == 1
    assert message.transport == "websocket"
    assert message.features == {"mcp": True}
    assert message.audio_params.sample_rate == 16000
    assert message.audio_params.frame_duration == 60


def test_listen_exposes_state_and_mode() -> None:
    start = protocol.parse_device_message(json.dumps(load("listen_start.json")))
    stop = protocol.parse_device_message(json.dumps(load("listen_stop.json")))
    detect = protocol.parse_device_message(json.dumps(load("listen_detect.json")))

    assert (start.state, start.mode) == ("start", "manual")
    assert (stop.state, stop.mode) == ("stop", None)
    assert (detect.state, detect.text) == ("detect", "你好小明")


def test_abort_exposes_the_reason() -> None:
    message = protocol.parse_device_message(json.dumps(load("abort.json")))

    assert isinstance(message, protocol.Abort)
    assert message.reason == "wake_word_detected"


def test_mcp_carries_the_payload_verbatim() -> None:
    raw = load("mcp_initialize_result.json")

    message = protocol.parse_device_message(json.dumps(raw))

    assert isinstance(message, protocol.Mcp)
    assert message.payload == raw["payload"]


@pytest.mark.parametrize(
    "text",
    ["", "not json", "{", "[]", '"hello"', "null", "42"],
)
def test_malformed_frames_raise_protocol_error(text: str) -> None:
    with pytest.raises(ProtocolError):
        protocol.parse_device_message(text)


def test_missing_type_raises_protocol_error() -> None:
    with pytest.raises(ProtocolError) as excinfo:
        protocol.parse_device_message('{"version": 1}')

    assert "type" in str(excinfo.value)


def test_non_string_type_raises_protocol_error() -> None:
    with pytest.raises(ProtocolError):
        protocol.parse_device_message('{"type": 7}')


def test_unknown_type_is_ignored_not_fatal(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="jarvis_gateway.protocol"):
        message = protocol.parse_device_message('{"type": "system", "command": "reboot"}')

    assert message is None
    unknown = [r for r in caplog.records if "unknown" in r.getMessage().lower()]
    assert len(unknown) == 1


def test_invalid_listen_state_raises_protocol_error() -> None:
    with pytest.raises(ProtocolError):
        protocol.parse_device_message('{"type": "listen", "state": "pause"}')


def test_mcp_payload_must_be_an_object() -> None:
    with pytest.raises(ProtocolError):
        protocol.parse_device_message('{"type": "mcp", "payload": []}')


# --- server messages -------------------------------------------------------


def test_server_hello_matches_fixture() -> None:
    raw = load("hello_server.json")

    built = protocol.server_hello(raw["session_id"])

    assert built == raw


def test_stt_message_matches_fixture() -> None:
    raw = load("stt.json")

    assert protocol.stt_message(raw["session_id"], raw["text"]) == raw


def test_llm_message_matches_fixture() -> None:
    raw = load("llm.json")

    assert protocol.llm_message(raw["session_id"], raw["emotion"], raw["text"]) == raw


@pytest.mark.parametrize("name", ["tts_start.json", "tts_sentence_start.json", "tts_stop.json"])
def test_tts_messages_match_fixtures(name: str) -> None:
    raw = load(name)
    builders = {
        "tts_start.json": lambda session_id: protocol.tts_start(session_id),
        "tts_sentence_start.json": lambda session_id: protocol.tts_sentence_start(
            session_id, raw["text"]
        ),
        "tts_stop.json": lambda session_id: protocol.tts_stop(session_id),
    }

    assert builders[name](raw["session_id"]) == raw


@pytest.mark.parametrize("name", ["mcp_initialize_request.json", "mcp_tools_call_request.json"])
def test_outgoing_mcp_matches_fixture(name: str) -> None:
    raw = load(name)

    assert protocol.mcp_message(raw["session_id"], raw["payload"]) == raw


@pytest.mark.parametrize(
    "builder",
    [
        lambda: protocol.server_hello(""),
        lambda: protocol.stt_message("", "hi"),
        lambda: protocol.llm_message("", "neutral", "hi"),
        lambda: protocol.tts_start(""),
        lambda: protocol.tts_sentence_start("", "hi"),
        lambda: protocol.tts_stop(""),
        lambda: protocol.mcp_message("", {"jsonrpc": "2.0"}),
    ],
)
def test_every_outgoing_message_enforces_a_session_id(builder) -> None:
    with pytest.raises(ProtocolError):
        builder()


def test_session_id_must_be_a_string() -> None:
    with pytest.raises(ProtocolError):
        protocol.stt_message(123, "hi")  # type: ignore[arg-type]
