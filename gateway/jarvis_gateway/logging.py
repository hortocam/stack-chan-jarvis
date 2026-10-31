"""Structured logging and per-turn timing for the gateway (T005).

Logs are JSON lines: one object per line, one event per object. A turn's
transcript is personal data and stays out of the log unless
`JG_LOG_TRANSCRIPTS=true` (FR-016), so the formatter drops it by default.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

__all__ = ["JsonLinesFormatter", "TurnTimer"]

# LogRecord attributes that are already represented by the four fixed keys.
_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
) | {"message", "asctime", "taskName"}

# Extra fields that carry what the user said or heard.
_PRIVATE_ATTRS = frozenset({"transcript"})


class JsonLinesFormatter(logging.Formatter):
    """Render a `LogRecord` as a single-line JSON object."""

    def __init__(self, include_transcripts: bool = False) -> None:
        super().__init__()
        self.include_transcripts = include_transcripts

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="microseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key.startswith("_"):
                continue
            if key in _PRIVATE_ATTRS and not self.include_transcripts:
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TurnTimer:
    """Record named stages of one turn as monotonic milliseconds from its start.

    `time.monotonic` is injected so tests can drive it; the service uses the
    default. Deltas are measured from the previous stage, which is what the
    latency budget in the plan is expressed in.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._start = clock()
        self._stages: dict[str, float] = {}

    def mark(self, stage: str) -> float:
        """Record `stage` now and return its milliseconds since the turn started."""
        elapsed = (self._clock() - self._start) * 1000.0
        self._stages[stage] = elapsed
        return elapsed

    @property
    def stages(self) -> dict[str, float]:
        """Stage name -> milliseconds since the turn started, in mark order."""
        return dict(self._stages)

    @property
    def deltas(self) -> dict[str, float]:
        """Stage name -> milliseconds since the previous stage."""
        out: dict[str, float] = {}
        previous = 0.0
        for stage, elapsed in self._stages.items():
            out[stage] = elapsed - previous
            previous = elapsed
        return out

    @property
    def elapsed_ms(self) -> float:
        """Milliseconds since the turn started."""
        return (self._clock() - self._start) * 1000.0
