"""The UI event bus: bounded, coalescing, rate-capped, honest when alone.

``self.ui.*`` calls are cheap but the screen is small and the socket is
shared with the voice path. This module owns the only queue between a lab
event and the device, and it enforces three rules:

* bounded — the queue has a maximum length and drops its oldest entry;
* coalescing — ``state``, ``clocks`` and ``globe_focus`` replace their own
  pending value, and an identical log line replaces itself;
* capped — at most ``max_per_second`` calls are dispatched in any rolling
  second, so a webhook storm cannot flood the screen.

When no device is attached, ``publish`` drops immediately with a debug line.
Nothing is buffered, so a reconnect never receives a backlog (the card's
honest-degradation rule).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .ui import UiError, build_ui_call, clocks_arguments

__all__ = [
    "ClockTicker",
    "RateLimiter",
    "UiEvent",
    "UiEventBus",
    "next_minute_delay",
]

logger = logging.getLogger(__name__)

_LATEST_WINS = frozenset({"state", "clocks", "globe_focus"})
_KINDS = frozenset({"globe_focus", "log_line", "state", "clocks"})


@dataclass(frozen=True, slots=True)
class UiEvent:
    """One already-validated UI call, ready for the bus."""

    kind: str
    arguments: dict[str, Any]

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise UiError(f"unknown UI event kind: {self.kind}")
        call = build_ui_call(f"self.ui.{self.kind}", self.arguments)
        object.__setattr__(self, "arguments", call["arguments"])

    @property
    def name(self) -> str:
        return f"self.ui.{self.kind}"

    def to_call(self) -> dict[str, Any]:
        return build_ui_call(self.name, self.arguments)


class RateLimiter:
    """Sliding one-second window: at most ``max_per_second`` dispatches."""

    def __init__(self, max_per_second: int) -> None:
        if max_per_second < 1:
            raise ValueError("max_per_second must be at least 1")
        self.max_per_second = max_per_second
        self._sent: deque[float] = deque()

    def allow(self, now: float) -> bool:
        while self._sent and now - self._sent[0] >= 1.0:
            self._sent.popleft()
        if len(self._sent) >= self.max_per_second:
            return False
        self._sent.append(now)
        return True


def next_minute_delay(now: float) -> float:
    """Seconds until the next whole minute; never zero."""

    remainder = now % 60.0
    return 60.0 if remainder == 0.0 else 60.0 - remainder


class ClockTicker:
    """Pushes ``self.ui.clocks`` on the minute, aligned to the wall clock."""

    def __init__(
        self,
        bus: Any,
        *,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._bus = bus
        self._now = now
        self._sleep = sleep

    async def run(self) -> None:
        while True:
            await self._sleep(next_minute_delay(self._now()))
            self._bus.publish_clocks(self._now())


class UiEventBus:
    """The single queue between lab events and one attached device channel."""

    def __init__(
        self,
        *,
        max_queue: int = 64,
        max_per_second: int = 4,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_queue < 1:
            raise ValueError("max_queue must be at least 1")
        self.max_queue = max_queue
        self._clock = clock
        self._rate = RateLimiter(max_per_second)
        self._queue: OrderedDict[tuple[Any, ...], UiEvent] = OrderedDict()
        self._channel: Any | None = None
        self._wake = asyncio.Event()
        self._pump: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._dispatched = 0
        self._coalesced = 0
        self._dropped_no_device = 0
        self._dropped_queue_full = 0
        self._dropped_rate = 0

    # -- attachment ------------------------------------------------------

    @property
    def channel(self) -> Any | None:
        return self._channel

    def attach(self, channel: Any) -> None:
        """Bind a device channel. A new attachment starts with an empty queue."""

        self._channel = channel
        self._queue.clear()

    def detach(self, channel: Any | None = None) -> None:
        if channel is not None and self._channel is not channel:
            return
        self._channel = None
        self._queue.clear()

    # -- publishing ------------------------------------------------------

    def publish(self, event: UiEvent) -> bool:
        """Queue one event; ``False`` when there is no device to show it on."""

        if self._channel is None or self._channel.closed:
            self._dropped_no_device += 1
            logger.debug("dropping UI event %s: no device connected", event.kind)
            return False

        key = self._key(event)
        if key in self._queue:
            del self._queue[key]
            self._coalesced += 1
        if len(self._queue) >= self.max_queue:
            self._queue.popitem(last=False)
            self._dropped_queue_full += 1
        self._queue[key] = event
        self._wake.set()
        return True

    def publish_source(self, source: str, payload: dict[str, Any]) -> int:
        """Parse one source payload and queue every event it produces."""

        from .ui_sources import SOURCE_PARSERS

        parser = SOURCE_PARSERS[source]
        events = parser(payload)
        return sum(1 for event in events if self.publish(event))

    def publish_clocks(self, now: float | None = None) -> bool:
        return self.publish(UiEvent("clocks", clocks_arguments(now)))

    # -- inspection ------------------------------------------------------

    @property
    def pending(self) -> int:
        return len(self._queue)

    @property
    def pending_events(self) -> tuple[UiEvent, ...]:
        return tuple(self._queue.values())

    @property
    def in_flight(self) -> int:
        return sum(1 for task in self._tasks if not task.done())

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "attached": self._channel is not None,
            "pending": self.pending,
            "in_flight": self.in_flight,
            "dispatched": self._dispatched,
            "coalesced": self._coalesced,
            "dropped_no_device": self._dropped_no_device,
            "dropped_queue_full": self._dropped_queue_full,
            "dropped_rate": self._dropped_rate,
        }

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        if self._pump is None:
            self._pump = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump
            self._pump = None
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._queue:
                channel = self._channel
                if channel is None or channel.closed:
                    self._dropped_no_device += len(self._queue)
                    self._queue.clear()
                    break
                _, event = self._queue.popitem(last=False)
                if not self._rate.allow(self._clock()):
                    self._dropped_rate += 1
                    continue
                task = asyncio.create_task(channel.call(event.name, event.arguments))
                self._tasks.add(task)
                task.add_done_callback(self._finished)
                self._dispatched += 1

    def _finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        with contextlib.suppress(asyncio.CancelledError, Exception):
            task.exception()

    @staticmethod
    def _key(event: UiEvent) -> tuple[Any, ...]:
        if event.kind in _LATEST_WINS:
            return (event.kind,)
        return (event.kind, event.arguments.get("text", ""))

    def __len__(self) -> int:
        return len(self._queue)

    def __iter__(self) -> Iterable[UiEvent]:
        return iter(self._queue.values())
