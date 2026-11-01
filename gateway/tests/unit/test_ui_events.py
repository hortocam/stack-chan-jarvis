"""Unit tests for the UI event bus: bounded, coalescing, rate-capped, honest.

The screen must never be flooded and must never be fed a backlog that was
collected while the robot was away. These tests drive the bus directly with a
recording channel, so the properties are asserted without a socket.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis_gateway.ui import UI_LOG_LINE, UI_STATE
from jarvis_gateway.ui_events import (
    ClockTicker,
    RateLimiter,
    UiEvent,
    UiEventBus,
    next_minute_delay,
)


class RecordingChannel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    async def call(self, name: str, arguments: dict) -> bool:
        self.calls.append((name, dict(arguments)))
        return True

    def close(self, reason: str = "closed") -> None:
        self.closed = True


async def _drain(bus: UiEventBus, timeout: float = 2.0) -> None:  # noqa: ASYNC109
    async def _wait() -> None:
        while bus.pending or bus.in_flight:  # noqa: ASYNC110
            await asyncio.sleep(0.005)

    await asyncio.wait_for(_wait(), timeout)


def test_event_validates_at_construction() -> None:
    with pytest.raises(ValueError, match="name"):
        UiEvent("state", {"name": "dancing"})


def test_log_line_events_with_the_same_text_coalesce() -> None:
    bus = UiEventBus()
    bus.attach(RecordingChannel())

    assert bus.publish(UiEvent("log_line", {"text": "same"})) is True
    assert bus.publish(UiEvent("log_line", {"text": "same"})) is True
    assert bus.publish(UiEvent("log_line", {"text": "different"})) is True

    assert bus.pending == 2
    assert bus.stats["coalesced"] == 1


def test_state_and_clocks_coalesce_to_the_latest_value() -> None:
    bus = UiEventBus()
    bus.attach(RecordingChannel())

    bus.publish(UiEvent("state", {"name": "idle"}))
    bus.publish(UiEvent("state", {"name": "thinking"}))
    bus.publish(UiEvent("state", {"name": "speaking"}))

    assert bus.pending == 1
    assert bus.stats["coalesced"] == 2


def test_no_device_drops_immediately_and_never_buffers_a_backlog() -> None:
    """The honest-degradation rule: no channel, no queue, no replay later."""

    bus = UiEventBus()
    for index in range(10):
        assert bus.publish(UiEvent("log_line", {"text": f"line {index}"})) is False

    assert bus.pending == 0
    assert bus.stats["dropped_no_device"] == 10


async def test_attach_after_drop_does_not_replay_the_backlog() -> None:
    bus = UiEventBus()
    for index in range(10):
        bus.publish(UiEvent("log_line", {"text": f"line {index}"}))

    channel = RecordingChannel()
    bus.attach(channel)
    await bus.start()
    try:
        await asyncio.sleep(0.02)
    finally:
        await bus.stop()

    assert channel.calls == []


def test_queue_is_bounded_and_drops_the_oldest() -> None:
    bus = UiEventBus(max_queue=4)
    bus.attach(RecordingChannel())

    for index in range(10):
        bus.publish(UiEvent("log_line", {"text": f"line {index}"}))

    assert bus.pending == 4
    assert bus.stats["dropped_queue_full"] == 6


async def test_flood_is_capped_per_second_and_the_rest_is_dropped() -> None:
    bus = UiEventBus(max_queue=64, max_per_second=4)
    channel = RecordingChannel()
    bus.attach(channel)
    await bus.start()
    try:
        for index in range(100):
            bus.publish(UiEvent("log_line", {"text": f"line {index}"}))
        await _drain(bus)
    finally:
        await bus.stop()

    assert len(channel.calls) == 4
    assert bus.stats["dispatched"] == 4
    assert bus.stats["dropped_rate"] == 60
    assert bus.stats["dropped_queue_full"] == 36


async def test_coalesced_state_is_the_one_delivered() -> None:
    bus = UiEventBus()
    channel = RecordingChannel()
    bus.attach(channel)
    bus.publish(UiEvent("state", {"name": "idle"}))
    bus.publish(UiEvent("state", {"name": "attention"}))

    await bus.start()
    try:
        await _drain(bus)
    finally:
        await bus.stop()

    assert channel.calls == [("self.ui.state", {"name": "attention"})]


def test_rate_limiter_uses_a_rolling_one_second_window() -> None:
    limiter = RateLimiter(max_per_second=3)

    assert [limiter.allow(0.0) for _ in range(4)] == [True, True, True, False]
    assert limiter.allow(0.999) is False
    assert limiter.allow(1.001) is True


def test_next_minute_delay_aligns_to_the_minute() -> None:
    assert next_minute_delay(100.0) == 20.0
    assert next_minute_delay(120.0) == 60.0
    assert next_minute_delay(0.5) == 59.5


async def test_clock_ticker_publishes_on_the_minute() -> None:
    class StubBus:
        def __init__(self) -> None:
            self.published: list[float] = []

        def publish_clocks(self, now: float | None = None) -> bool:
            self.published.append(now if now is not None else -1.0)
            return True

    now = [100.0]
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        now[0] += delay
        await asyncio.sleep(0)

    bus = StubBus()
    ticker = ClockTicker(bus, now=lambda: now[0], sleep=fake_sleep)
    task = asyncio.create_task(ticker.run())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert delays[:2] == [20.0, 60.0]
    assert bus.published[:2] == [120.0, 180.0]


def test_published_clocks_use_the_documented_shape() -> None:
    bus = UiEventBus()
    bus.attach(RecordingChannel())

    assert bus.publish_clocks(0.0) is True

    event = bus.pending_events[0]
    assert event.kind == "clocks"
    assert set(event.arguments) == {"utc", "local", "tz"}


def test_log_line_and_state_names_are_the_wire_names() -> None:
    assert UiEvent("log_line", {"text": "x"}).name == UI_LOG_LINE
    assert UiEvent("state", {"name": "idle"}).name == UI_STATE
