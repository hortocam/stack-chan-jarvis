"""Unit tests for ``UiChannel`` — one best-effort MCP call over the voice socket.

A UI call is a JSON-RPC ``tools/call`` inside the existing ``type: "mcp"``
frame. It may not stall anything: no response inside the deadline is a debug
line and a dropped call, and a device error is logged, not raised.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from jarvis_gateway.ui import UI_LOG_LINE, UiChannel, UiError


class RecordingSender:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)


async def _until(predicate, timeout: float = 1.0) -> None:  # noqa: ASYNC109
    async def _poll() -> None:
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0)

    await asyncio.wait_for(_poll(), timeout)


def _id(message: dict) -> int:
    return message["payload"]["id"]


async def test_call_sends_the_mcp_tools_call_envelope_and_returns_true() -> None:
    sender = RecordingSender()
    channel = UiChannel(sender, session_id="session-1")
    task = asyncio.create_task(channel.call(UI_LOG_LINE, {"text": "hello"}))
    await _until(lambda: bool(sender.messages))

    message = sender.messages[0]
    assert message["type"] == "mcp"
    assert message["session_id"] == "session-1"
    assert message["payload"]["jsonrpc"] == "2.0"
    assert message["payload"]["method"] == "tools/call"
    assert message["payload"]["params"] == {
        "name": "self.ui.log_line",
        "arguments": {"text": "hello"},
    }
    assert isinstance(_id(message), int)

    assert channel.handle_payload(
        {"jsonrpc": "2.0", "id": _id(message), "result": {"content": [], "isError": False}}
    )
    assert await task is True
    assert channel.stats.sent == 1
    assert channel.stats.ok == 1


async def test_timeout_is_logged_and_dropped(caplog) -> None:
    sender = RecordingSender()
    channel = UiChannel(sender, session_id="s", timeout_s=0.02)

    with caplog.at_level(logging.DEBUG, logger="jarvis_gateway.ui"):
        assert await channel.call(UI_LOG_LINE, {"text": "nobody answers"}) is False

    assert channel.stats.timeouts == 1
    assert "timed out" in caplog.text


async def test_device_error_response_is_logged_and_dropped(caplog) -> None:
    sender = RecordingSender()
    channel = UiChannel(sender, session_id="s", timeout_s=1.0)
    task = asyncio.create_task(channel.call(UI_LOG_LINE, {"text": "unknown"}))
    await _until(lambda: bool(sender.messages))

    with caplog.at_level(logging.WARNING, logger="jarvis_gateway.ui"):
        assert channel.handle_payload(
            {
                "jsonrpc": "2.0",
                "id": _id(sender.messages[0]),
                "error": {"code": -32601, "message": "Unknown tool: self.ui.log_line"},
            }
        )
    assert await task is False
    assert channel.stats.errors == 1
    assert "Unknown tool" in caplog.text


async def test_is_error_result_counts_as_an_error() -> None:
    sender = RecordingSender()
    channel = UiChannel(sender, session_id="s")
    task = asyncio.create_task(channel.call(UI_LOG_LINE, {"text": "boom"}))
    await _until(lambda: bool(sender.messages))

    channel.handle_payload(
        {"jsonrpc": "2.0", "id": _id(sender.messages[0]), "result": {"isError": True}}
    )

    assert await task is False
    assert channel.stats.errors == 1


async def test_unknown_or_late_response_id_is_ignored() -> None:
    channel = UiChannel(RecordingSender(), session_id="s")

    assert channel.handle_payload({"jsonrpc": "2.0", "id": 999, "result": {}}) is False


async def test_invalid_call_never_reaches_the_socket() -> None:
    """Negative control: an unknown tool is refused in the gateway."""

    sender = RecordingSender()
    channel = UiChannel(sender, session_id="s")

    with pytest.raises(UiError, match="self.ui.reboot"):
        await channel.call("self.ui.reboot", {})
    assert sender.messages == []


async def test_closed_channel_drops_without_sending() -> None:
    sender = RecordingSender()
    channel = UiChannel(sender, session_id="s")
    channel.close("session closed")

    assert await channel.call(UI_LOG_LINE, {"text": "after close"}) is False
    assert sender.messages == []
    assert channel.stats.dropped == 1
