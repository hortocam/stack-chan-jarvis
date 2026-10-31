"""Fixture hygiene for the vendor protocol captures (T002).

This is not the protocol contract test — T007 owns round-trip behaviour. Here we
only assert that the fixture set the later phases load is complete, parseable,
and carries the envelope every non-handshake message must carry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PROTOCOL_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "protocol"

DEVICE_TO_SERVER = [
    "hello_device.json",
    "listen_start.json",
    "listen_stop.json",
    "listen_detect.json",
    "abort.json",
    "mcp_initialize_request.json",
    "mcp_tools_call_request.json",
]

SERVER_TO_DEVICE = [
    "hello_server.json",
    "stt.json",
    "llm.json",
    "tts_start.json",
    "tts_sentence_start.json",
    "tts_stop.json",
    "mcp_initialize_result.json",
    "mcp_tools_list_result.json",
    "mcp_tools_call_result.json",
]

OTHER = ["ota_response.json"]

ALL_FIXTURES = DEVICE_TO_SERVER + SERVER_TO_DEVICE + OTHER


def load(name: str) -> dict:
    return json.loads((PROTOCOL_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_fixture_exists_and_is_a_json_object(name: str) -> None:
    path = PROTOCOL_DIR / name

    assert path.is_file(), f"missing T002 fixture {name}"
    assert isinstance(load(name), dict)


SESSION_FIXTURES = [
    name
    for name in DEVICE_TO_SERVER + SERVER_TO_DEVICE
    if name != "hello_device.json"  # sent before the server issues a session id
]


@pytest.mark.parametrize("name", SESSION_FIXTURES)
def test_session_messages_carry_the_session_id(name: str) -> None:
    document = load(name)

    assert document["session_id"] == "0123456789abcdef0123456789abcdef"
    assert isinstance(document["type"], str)


def test_device_hello_has_no_session_id_yet() -> None:
    # The device sends hello before the server has issued a session id, and the
    # vendor's GetHelloMessage() carries none.
    document = load("hello_device.json")

    assert "session_id" not in document


def test_device_hello_uses_the_16k_uplink_params() -> None:
    document = load("hello_device.json")

    assert document["type"] == "hello"
    assert document["version"] == 1
    assert document["transport"] == "websocket"
    assert document["features"] == {"mcp": True}
    assert document["audio_params"] == {
        "format": "opus",
        "sample_rate": 16000,
        "channels": 1,
        "frame_duration": 60,
    }


def test_server_hello_uses_the_24k_downlink_params() -> None:
    document = load("hello_server.json")

    assert document["transport"] == "websocket"
    assert document["audio_params"]["sample_rate"] == 24000
    assert document["audio_params"]["frame_duration"] == 60


@pytest.mark.parametrize("name", [n for n in ALL_FIXTURES if n.startswith("mcp_")])
def test_mcp_fixtures_wrap_a_jsonrpc_20_payload(name: str) -> None:
    document = load(name)

    assert document["type"] == "mcp"
    assert document["payload"]["jsonrpc"] == "2.0"
    assert isinstance(document["payload"]["id"], int)


def test_ota_fixture_is_the_three_block_manifest_with_no_cloud_keys() -> None:
    document = load("ota_response.json")

    assert set(document) == {"firmware", "websocket", "server_time"}
    assert set(document["firmware"]) == {"version", "url"}
    assert set(document["websocket"]) == {"url", "token", "version"}
    assert set(document["server_time"]) == {"timestamp", "timezone_offset"}
    assert "activation" not in document
    assert "mqtt" not in document
    # Epoch milliseconds, not seconds: a seconds-shaped value would be ~1.7e9.
    assert document["server_time"]["timestamp"] > 1_000_000_000_000
    assert document["server_time"]["timezone_offset"] == -240


def test_every_fixture_is_documented_in_the_provenance_note() -> None:
    sources = (PROTOCOL_DIR / "SOURCES.md").read_text(encoding="utf-8")

    missing = [name for name in ALL_FIXTURES if name not in sources]
    assert missing == []
