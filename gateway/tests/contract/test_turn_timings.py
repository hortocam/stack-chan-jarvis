"""Per-turn timing records (T028, FR-016, plan "Latency Budget Mapping").

One structured record per spoken turn carrying every stage the latency budget
names, cumulative from the end of the utterance and therefore monotonic. A turn
that produced no speech has no timing record at all, and the default formatter
never writes the transcript or the reply.
"""

from __future__ import annotations

import logging
from pathlib import Path

from jarvis_gateway.logging import JsonLinesFormatter

AUDIO = Path(__file__).resolve().parents[1] / "fixtures" / "audio"

STAGES = ["vad_end_ms", "stt_ms", "ttft_ms", "first_audio_ms", "tts_end_ms"]


async def _run_one_turn(harness) -> None:
    device = await harness.connect()
    try:
        await device.listen("start")
        await device.stream_wav(AUDIO / "utterance_ok.wav")
        await device.listen("stop")
        await device.wait_for(lambda m: m.get("state") == "stop", timeout=10)
    finally:
        await device.close()


def _turn_records(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == "turn"]


async def test_a_spoken_turn_records_every_stage(harness, caplog) -> None:
    caplog.set_level(logging.INFO, logger="jarvis_gateway.turn")
    harness.whisper.text = "what time is it"
    harness.hermes.tokens = ["It is nine fifteen.", " The evening is young."]

    await _run_one_turn(harness)

    records = _turn_records(caplog)
    assert len(records) == 1
    record = records[0]
    timings = record.timings
    assert list(timings) == STAGES
    values = list(timings.values())
    assert values == sorted(values), f"stage timings went backwards: {timings}"
    assert timings["vad_end_ms"] >= 0
    assert record.frames > 0
    assert record.sentences >= 1
    assert record.outcome == "spoken"

    # Default privacy: neither what the user said nor what the robot said is logged.
    rendered = JsonLinesFormatter().format(record)
    assert "what time is it" not in rendered
    assert "nine fifteen" not in rendered
    assert '"timings"' in rendered


async def test_a_silent_turn_has_no_timing_record(harness, caplog) -> None:
    caplog.set_level(logging.INFO, logger="jarvis_gateway.turn")
    from tests.contract.test_empty_utterance import opus_packets

    device = await harness.connect()
    try:
        await device.listen("start")
        await device.send_packets(opus_packets("silence_2s.wav"))
        await device.listen("stop")
        assert await device.wait_quiet(0.4)
    finally:
        await device.close()

    assert _turn_records(caplog) == []


async def test_an_empty_transcript_has_no_timing_record(harness, caplog) -> None:
    caplog.set_level(logging.INFO, logger="jarvis_gateway.turn")
    harness.whisper.mode = "empty"

    device = await harness.connect()
    try:
        await device.listen("start")
        await device.stream_wav(AUDIO / "utterance_ok.wav")
        await device.listen("stop")
        assert await device.wait_quiet(0.4)
    finally:
        await device.close()

    assert device.log.after_hello() == []
    assert _turn_records(caplog) == []
