"""``listen stop`` in manual mode ends the utterance at once (T029, FR-005).

The device's own stop is the second half of the end-of-speech decision: it must
close the utterance immediately (no waiting out the silence tail), and a second
stop for the same utterance must be ignored rather than starting another turn.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

AUDIO = Path(__file__).resolve().parents[1] / "fixtures" / "audio"


async def test_stop_closes_the_utterance_immediately(harness_factory) -> None:
    harness = await harness_factory(JG_VAD_MIN_SPEECH_MS="100", JG_VAD_SILENCE_MS="3000")
    harness.whisper.text = "what time is it"
    device = await harness.connect()
    try:
        await device.listen("start")
        # ~240 ms of speech, short of the 3 s silence tail: only the stop can end it.
        await device.stream_wav(AUDIO / "utterance_ok.wav", count=4)
        started = asyncio.get_running_loop().time()
        await device.listen("stop")
        await device.wait_for(lambda m: m.get("type") == "stt", timeout=5)
        elapsed = asyncio.get_running_loop().time() - started

        assert elapsed < 0.25, f"the stop took {elapsed * 1000:.0f} ms to close the utterance"
        await device.wait_for(lambda m: m.get("state") == "stop", timeout=10)
    finally:
        await device.close()

    assert harness.whisper.requests, "stop did not hand the utterance to whisper"


async def test_a_later_stop_for_the_same_utterance_is_ignored(harness_factory) -> None:
    harness = await harness_factory(JG_VAD_MIN_SPEECH_MS="100")
    device = await harness.connect()
    try:
        await device.listen("start")
        await device.stream_wav(AUDIO / "utterance_ok.wav", count=4)
        await device.listen("stop")
        await device.listen("stop")  # a duplicate for the utterance already closed
        await device.wait_for(lambda m: m.get("state") == "stop", timeout=10)
        assert await device.wait_quiet(0.3) is True

        messages = device.log.after_hello()
        assert [m["type"] for m in messages].count("stt") == 1
        assert [m["type"] for m in messages].count("llm") == 1
        assert [m.get("state") for m in messages if m.get("type") == "tts"].count("stop") == 1
    finally:
        await device.close()

    assert len(harness.whisper.requests) == 1


async def test_stop_while_thinking_is_not_a_second_turn(harness_factory) -> None:
    harness = await harness_factory()
    harness.tts.delay_s = 0.0
    device = await harness.connect()
    try:
        await device.listen("start")
        await device.stream_wav(AUDIO / "utterance_ok.wav")
        await device.listen("stop")
        await device.wait_for(lambda m: m.get("type") == "stt", timeout=10)
        await device.listen("stop")  # arrives mid-turn
        await device.wait_for(lambda m: m.get("state") == "stop", timeout=10)
        assert await device.wait_quiet(0.3) is True

        messages = device.log.after_hello()
        assert [m["type"] for m in messages].count("stt") == 1
    finally:
        await device.close()
