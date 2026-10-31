"""Handshake and application shell contract (T015).

The firmware sends four headers on the upgrade (plan C2). Auth is decided
before any protocol byte is exchanged, and a rejection creates no session.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiohttp
import pytest
from aiohttp import WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

from jarvis_gateway.config import Config, load_config
from jarvis_gateway.session import SessionState
from jarvis_gateway.ws_app import REGISTRY, create_app

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "protocol"
DEVICE_ID = "aa:bb:cc:dd:ee:ff"
CLIENT_ID = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"

HEADERS = {
    "Authorization": "Bearer test-token",
    "Protocol-Version": "1",
    "Device-Id": DEVICE_ID,
    "Client-Id": CLIENT_ID,
}

ENV = {
    "JG_DEVICE_TOKEN": "test-token",
    "JG_HERMES_API_KEY": "brain-key",
    "JG_PUBLIC_BASE_URL": "http://172.16.90.14:8010",
}


@pytest.fixture
def config() -> Config:
    return load_config(dict(ENV))


@pytest.fixture
async def gateway(config: Config):
    app = create_app(config, hello_timeout=0.25)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client, app
    finally:
        await client.close()


def hello_message() -> dict:
    return json.loads((FIXTURES / "hello_device.json").read_text(encoding="utf-8"))


async def test_valid_handshake_gets_a_server_hello_with_a_session(gateway) -> None:
    client, app = gateway

    ws = await client.ws_connect("/xiaozhi/v1/", headers=HEADERS)
    await ws.send_str(json.dumps(hello_message()))
    reply = json.loads((await ws.receive()).data)

    assert reply["type"] == "hello"
    assert reply["transport"] == "websocket"
    assert len(reply["session_id"]) == 32
    int(reply["session_id"], 16)
    assert reply["audio_params"] == {
        "format": "opus",
        "sample_rate": 24000,
        "channels": 1,
        "frame_duration": 60,
    }
    session = app[REGISTRY].get(DEVICE_ID)
    assert session is not None
    assert session.session_id == reply["session_id"]
    assert session.state is SessionState.IDLE
    await ws.close()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {
            "Authorization": "Bearer wrong-token",
            **{k: v for k, v in HEADERS.items() if k != "Authorization"},
        },
        {
            "Authorization": "Basic test-token",
            **{k: v for k, v in HEADERS.items() if k != "Authorization"},
        },
        {
            "Authorization": "Bearer ",
            **{k: v for k, v in HEADERS.items() if k != "Authorization"},
        },
    ],
)
async def test_rejected_token_gets_401_and_creates_no_session(gateway, headers) -> None:
    client, app = gateway

    with pytest.raises(WSServerHandshakeError) as excinfo:
        await client.ws_connect("/xiaozhi/v1/", headers=headers)

    assert excinfo.value.status == 401
    assert len(app[REGISTRY]) == 0


@pytest.mark.parametrize("missing", ["Protocol-Version", "Device-Id", "Client-Id"])
async def test_missing_required_header_gets_400(gateway, missing: str) -> None:
    client, app = gateway
    headers = {key: value for key, value in HEADERS.items() if key != missing}

    with pytest.raises(WSServerHandshakeError) as excinfo:
        await client.ws_connect("/xiaozhi/v1/", headers=headers)

    assert excinfo.value.status == 400
    assert len(app[REGISTRY]) == 0


async def test_binary_protocol_version_two_gets_426(gateway) -> None:
    client, app = gateway
    headers = dict(HEADERS, **{"Protocol-Version": "2"})

    with pytest.raises(WSServerHandshakeError) as excinfo:
        await client.ws_connect("/xiaozhi/v1/", headers=headers)

    assert excinfo.value.status == 426
    assert len(app[REGISTRY]) == 0


async def test_missing_hello_closes_1008(gateway) -> None:
    client, app = gateway

    ws = await client.ws_connect("/xiaozhi/v1/", headers=HEADERS)
    await ws.receive()

    assert ws.close_code == 1008
    assert len(app[REGISTRY]) == 0


async def test_binary_before_hello_closes_1003(gateway) -> None:
    client, app = gateway

    ws = await client.ws_connect("/xiaozhi/v1/", headers=HEADERS)
    await ws.send_bytes(b"\x01\x02\x03")
    await ws.receive()

    assert ws.close_code == 1003
    assert len(app[REGISTRY]) == 0


async def test_unparseable_json_before_hello_closes_1003(gateway) -> None:
    client, _ = gateway

    ws = await client.ws_connect("/xiaozhi/v1/", headers=HEADERS)
    await ws.send_str("{not json")
    await ws.receive()

    assert ws.close_code == 1003


async def test_unparseable_json_after_hello_closes_1003(gateway) -> None:
    client, app = gateway
    ws = await client.ws_connect("/xiaozhi/v1/", headers=HEADERS)
    await ws.send_str(json.dumps(hello_message()))
    await ws.receive()

    await ws.send_str("{not json")
    await ws.receive()

    assert ws.close_code == 1003
    assert len(app[REGISTRY]) == 0


async def test_unknown_message_type_is_ignored_and_the_session_lives(gateway) -> None:
    client, app = gateway
    ws = await client.ws_connect("/xiaozhi/v1/", headers=HEADERS)
    await ws.send_str(json.dumps(hello_message()))
    await ws.receive()

    await ws.send_str(json.dumps({"type": "system", "command": "reboot"}))
    await asyncio.sleep(0.05)

    assert not ws.closed
    assert app[REGISTRY].get(DEVICE_ID) is not None
    await ws.close()


async def test_healthz_reports_uptime_and_sessions(gateway) -> None:
    client, _ = gateway

    response = await client.get("/healthz")

    assert response.status == 200
    body = await response.json()
    assert set(body) >= {"status", "version", "uptime_s", "sessions"}
    assert body["status"] == "ok"
    assert body["sessions"] == 0


async def test_ota_stub_answers_get_and_post_with_the_configured_ws_url(gateway) -> None:
    client, _ = gateway

    for response in (
        await client.get("/xiaozhi/ota/"),
        await client.post("/xiaozhi/ota/", json={"application": {"version": "1.0.0"}}),
    ):
        assert response.status == 200
        body = await response.json()
        assert body["websocket"]["url"] == "ws://172.16.90.14:8010/xiaozhi/v1/"
        assert body["websocket"]["token"] == "test-token"
        assert body["server_time"]["timestamp"] > 0


async def test_a_second_connection_for_the_same_device_replaces_the_first(gateway) -> None:
    client, app = gateway
    first = await client.ws_connect("/xiaozhi/v1/", headers=HEADERS)
    await first.send_str(json.dumps(hello_message()))
    first_hello = json.loads((await first.receive()).data)

    second = await client.ws_connect("/xiaozhi/v1/", headers=HEADERS)
    await second.send_str(json.dumps(hello_message()))
    second_hello = json.loads((await second.receive()).data)

    assert first_hello["session_id"] != second_hello["session_id"]
    assert app[REGISTRY].get(DEVICE_ID).session_id == second_hello["session_id"]
    # The client only processes the server's close frame when it reads.
    message = await asyncio.wait_for(first.receive(), timeout=1.0)
    assert message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED)
    assert first.closed
    await second.close()
