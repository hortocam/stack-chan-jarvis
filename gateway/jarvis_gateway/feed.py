"""Headline pings for the globe — canned until a real news/markets source lands.

The owner's goal is a screen driven by the lab. The last source is external
headlines (news, markets); this module starts as a *canned* list behind
``JG_UI_FEED_ENABLED`` so the wire contract and the firmware can be built
before an API key or a network call exists. Nothing here invents live data:
the strings below are labelled canned and every event says so in the log.

A real feed later implements ``next_event``/``events`` the same way; no other
module changes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any

from .ui_events import UiEvent

__all__ = ["CANNED_HEADLINES", "CannedFeed", "FeedPump", "Headline"]


@dataclass(frozen=True, slots=True)
class Headline:
    """One location + one sentence for the globe card."""

    label: str
    headline: str
    lat: float
    lon: float


#: Deliberately canned. These are examples the firmware can render today, not
#: claims about the world; a live feed replaces this tuple behind the same API.
CANNED_HEADLINES: tuple[Headline, ...] = (
    Headline("Boston", "Lab: 359 tests green on CT 914", 42.3601, -71.0589),
    Headline("London", "Canned: FTSE 100 closes higher", 51.5072, -0.1276),
    Headline("Tokyo", "Canned: Nikkei 225 up 0.8%", 35.6762, 139.6503),
)


class CannedFeed:
    """A round-robin list of sample headline pings, disabled by default."""

    def __init__(
        self, *, enabled: bool = False, headlines: tuple[Headline, ...] = CANNED_HEADLINES
    ) -> None:
        if not headlines:
            raise ValueError("headlines must not be empty")
        self.enabled = enabled
        self.headlines = headlines
        self._index = 0

    def next_event(self) -> UiEvent | None:
        if not self.enabled:
            return None
        headline = self.headlines[self._index % len(self.headlines)]
        self._index += 1
        return UiEvent(
            "globe_focus",
            {
                "lat": headline.lat,
                "lon": headline.lon,
                "label": headline.label,
                "headline": headline.headline,
            },
        )

    def events(self) -> list[UiEvent]:
        if not self.enabled:
            return []
        return [
            UiEvent(
                "globe_focus",
                {
                    "lat": headline.lat,
                    "lon": headline.lon,
                    "label": headline.label,
                    "headline": headline.headline,
                },
            )
            for headline in self.headlines
        ]

    def __iter__(self) -> Iterator[UiEvent]:
        return iter(self.events())


class FeedPump:
    """Publish one headline every ``interval_s`` while the bus has a device."""

    def __init__(
        self,
        feed: CannedFeed,
        bus: Any,
        *,
        interval_s: float = 90.0,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self.feed = feed
        self.bus = bus
        self.interval_s = interval_s
        self._sleep = sleep

    async def run(self) -> None:
        while True:
            event = self.feed.next_event()
            if event is not None:
                self.bus.publish(event)
            await self._sleep(self.interval_s)
