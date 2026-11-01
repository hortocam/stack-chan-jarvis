"""End-to-end UI surface contract against the synthetic device.

No robot is needed: the fake device registers the four ``self.ui.*`` tools on
its MCP server, answers ``tools/call`` with the documented envelope, and the
gateway's event bus drives it over the one voice WebSocket.

The negative controls are here too — a tool the gateway does not know never
reaches the socket, and the device refuses a name it did not register.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis_gateway.feed import Headline
from jarvis_gateway.ui import (
    UI_CLOCKS,
    UI_GLOBE_FOCUS,
    UI_LOG_LINE,
    UI_STATE,
    UI_TOOLS,
    UiError,
)
from jarvis_gateway.ui_events import UiEvent
from jarvis_gateway.ui_sources import event_from_headline, events_from_kanban
from jarvis_gateway.ws_app import UI_BUS


async def _until(predicate, timeout: float = 1.0) -> None:  # noqa: ASYNC109
    async def _poll() -> None:
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.005)

    await asyncio.wait_for(_poll(), timeout)


def _payload(message: dict) -> dict:
    return message.get("payload") or {}


async def test_a_simulated_lab_event_reaches_the_fake_device(harness) -> None:
    device = await harness.connect()
    try:
        bus = harness.app[UI_BUS]
        assert bus.channel is not None

        assert bus.publish(UiEvent("log_line", {"text": "git: PR #11 merged"})) is True
        message = await device.wait_for_mcp_call(UI_LOG_LINE, timeout=1.0)

        assert message["type"] == "mcp"
        assert message["session_id"] == device.log.session_id
        payload = _payload(message)
        assert payload["jsonrpc"] == "2.0"
        assert payload["method"] == "tools/call"
        assert payload["params"] == {
            "name": "self.ui.log_line",
            "arguments": {"text": "git: PR #11 merged"},
        }
        assert isinstance(payload["id"], int)

        await _until(lambda: bus.channel.stats.ok == 1)
    finally:
        await device.close()


async def test_kanban_transition_raises_attention_then_logs(harness) -> None:
    device = await harness.connect()
    try:
        bus = harness.app[UI_BUS]
        events = events_from_kanban({"card": {"title": "UI backend"}, "to": "blocked"})
        for event in events:
            bus.publish(event)

        await device.wait_for_mcp_call(UI_STATE, timeout=1.0)
        await device.wait_for_mcp_call(UI_LOG_LINE, timeout=1.0)

        states = [
            _payload(m)["params"]["arguments"]["name"]
            for m in device.log.received_json
            if _payload(m).get("params", {}).get("name") == UI_STATE
        ]
        assert "attention" in states
    finally:
        await device.close()


async def test_headline_ping_becomes_a_globe_focus(harness) -> None:
    device = await harness.connect()
    try:
        bus = harness.app[UI_BUS]
        bus.publish(
            event_from_headline(
                Headline(label="Boston", headline="Lab: green", lat=42.36, lon=-71.06)
            )
        )

        message = await device.wait_for_mcp_call(UI_GLOBE_FOCUS, timeout=1.0)
        arguments = _payload(message)["params"]["arguments"]
        assert arguments == {
            "lat": 42.36,
            "lon": -71.06,
            "label": "Boston",
            "headline": "Lab: green",
            "hold_ms": 8000,
        }
    finally:
        await device.close()


async def test_unknown_ui_tool_is_refused_before_the_wire(harness) -> None:
    """Negative control: the gateway will not send a tool it does not own."""

    device = await harness.connect()
    try:
        channel = harness.app[UI_BUS].channel
        with pytest.raises(UiError, match="self.ui.reboot"):
            await channel.call("self.ui.reboot", {})

        await asyncio.sleep(0.05)
        names = [
            _payload(m).get("params", {}).get("name")
            for m in device.log.received_json
            if _payload(m).get("method") == "tools/call"
        ]
        assert "self.ui.reboot" not in names
    finally:
        await device.close()


async def test_fake_device_refuses_a_tool_it_did_not_register(harness) -> None:
    """The device half of the negative control, as the firmware would answer."""

    device = await harness.connect()
    try:
        await device._handle_mcp(  # noqa: SLF001 - exercising the test double's MCP server
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "self.ui.nope", "arguments": {}},
            }
        )
        await asyncio.sleep(0.05)

        errors = [m for m in device.log.sent_json if (m.get("payload") or {}).get("error")]
        assert errors, device.log.sent_json
        assert errors[0]["payload"]["error"]["code"] == -32601
        assert "Unknown tool" in errors[0]["payload"]["error"]["message"]
    finally:
        await device.close()


async def test_fake_device_registers_all_four_ui_tools(harness) -> None:
    device = await harness.connect()
    try:
        assert UI_TOOLS <= device.mcp_tools
        # Registration is load-bearing: test_a_simulated_lab_event asserts the
        # channel got a successful result, which an unregistered tool cannot give.
    finally:
        await device.close()


async def test_no_device_drops_events_and_never_replays_them(harness) -> None:
    bus = harness.app[UI_BUS]
    assert bus.channel is None

    for index in range(5):
        assert bus.publish(UiEvent("log_line", {"text": f"while away {index}"})) is False
    assert bus.stats["dropped_no_device"] == 5

    device = await harness.connect()
    try:
        await asyncio.sleep(0.15)
        assert [
            m for m in device.log.received_json if _payload(m).get("method") == "tools/call"
        ] == []
    finally:
        await device.close()


async def test_flooded_queue_is_capped_on_the_wire(harness) -> None:
    device = await harness.connect()
    try:
        bus = harness.app[UI_BUS]
        for index in range(100):
            bus.publish(UiEvent("log_line", {"text": f"flood {index}"}))

        await asyncio.sleep(0.4)
        log_calls = [
            m
            for m in device.log.received_json
            if _payload(m).get("params", {}).get("name") == UI_LOG_LINE
        ]

        assert 1 <= len(log_calls) <= 4
        assert bus.stats["dispatched"] <= 4
        assert bus.stats["dropped_rate"] + bus.stats["dropped_queue_full"] >= 96
        assert bus.pending == 0
    finally:
        await device.close()


async def test_clocks_can_be_pushed_by_hand(harness) -> None:
    device = await harness.connect()
    try:
        assert harness.app[UI_BUS].publish_clocks(0.0) is True
        message = await device.wait_for_mcp_call(UI_CLOCKS, timeout=1.0)
        arguments = _payload(message)["params"]["arguments"]
        assert set(arguments) == {"utc", "local", "tz"}
    finally:
        await device.close()
