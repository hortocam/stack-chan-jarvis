"""Unit tests for JSON-lines logging and the per-turn timer (T005).

Two things are load-bearing here: every line must be one parseable JSON object
(nothing downstream can scrape free text), and the transcript must stay out of
the logs unless `JG_LOG_TRANSCRIPTS` enables it (FR-016).
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime

import pytest

from jarvis_gateway.logging import JsonLinesFormatter, TurnTimer


class FakeClock:
    """Deterministic stand-in for time.monotonic."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _format_records(
    *records: tuple[int, str, dict], include_transcripts: bool = False
) -> list[str]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLinesFormatter(include_transcripts=include_transcripts))
    logger = logging.getLogger("test.logging.formatter")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    for level, message, extra in records:
        logger.log(level, message, extra=extra)

    return stream.getvalue().splitlines()


def test_one_json_object_per_line() -> None:
    lines = _format_records(
        (logging.INFO, "first", {"turn": 1}),
        (logging.WARNING, "second", {"turn": 2}),
        (logging.ERROR, "third", {"turn": 3}),
    )

    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert [obj["msg"] for obj in parsed] == ["first", "second", "third"]
    assert [obj["level"] for obj in parsed] == ["INFO", "WARNING", "ERROR"]
    assert [obj["turn"] for obj in parsed] == [1, 2, 3]
    assert all(obj["logger"] == "test.logging.formatter" for obj in parsed)


def test_timestamp_is_iso8601_with_a_timezone() -> None:
    (line,) = _format_records((logging.INFO, "hello", {}))

    parsed = json.loads(line)
    stamp = datetime.fromisoformat(parsed["ts"])
    assert stamp.tzinfo is not None
    assert stamp.year >= 2026
    # Seconds-level precision is not enough to order stages inside a turn.
    assert "." in parsed["ts"] or "+" in parsed["ts"]


def test_transcript_is_absent_unless_enabled() -> None:
    (line,) = _format_records((logging.INFO, "turn done", {"transcript": "what time is it"}))

    assert "transcript" not in json.loads(line)
    assert "what time is it" not in line


def test_transcript_appears_when_enabled() -> None:
    (line,) = _format_records(
        (logging.INFO, "turn done", {"transcript": "what time is it"}),
        include_transcripts=True,
    )

    assert json.loads(line)["transcript"] == "what time is it"


def test_timer_records_named_stages_in_order() -> None:
    clock = FakeClock()
    timer = TurnTimer(clock=clock)

    clock.advance(0.30)
    assert timer.mark("vad_end") == pytest.approx(300.0)
    clock.advance(0.20)
    assert timer.mark("stt") == pytest.approx(500.0)
    clock.advance(0.70)
    assert timer.mark("ttft") == pytest.approx(1200.0)
    clock.advance(0.30)
    assert timer.mark("first_audio") == pytest.approx(1500.0)

    assert list(timer.stages) == ["vad_end", "stt", "ttft", "first_audio"]
    assert timer.stages["ttft"] == pytest.approx(1200.0)
    assert timer.elapsed_ms == pytest.approx(1500.0)


def test_timer_reports_deltas_between_stages() -> None:
    clock = FakeClock()
    timer = TurnTimer(clock=clock)

    clock.advance(0.30)
    timer.mark("vad_end")
    clock.advance(0.20)
    timer.mark("stt")

    deltas = timer.deltas
    assert list(deltas) == ["vad_end", "stt"]
    assert deltas["vad_end"] == pytest.approx(300.0)
    assert deltas["stt"] == pytest.approx(200.0)
    assert sum(deltas.values()) == pytest.approx(timer.elapsed_ms)


def test_timer_remark_updates_value_without_duplicating_the_stage() -> None:
    clock = FakeClock()
    timer = TurnTimer(clock=clock)

    clock.advance(0.10)
    first = timer.mark("stt")
    clock.advance(0.40)
    second = timer.mark("stt")

    assert second > first
    assert list(timer.stages) == ["stt"]
    assert timer.stages["stt"] == pytest.approx(second)


def test_timer_deltas_are_never_negative() -> None:
    clock = FakeClock()
    timer = TurnTimer(clock=clock)
    for name in ("a", "b", "c"):
        timer.mark(name)
        clock.advance(0.05)

    assert all(delta >= 0.0 for delta in timer.deltas.values())
