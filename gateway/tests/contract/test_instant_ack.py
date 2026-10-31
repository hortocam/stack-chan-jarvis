"""Instant acknowledgement while the brain is thinking (P3.1 latency, SC-001).

When the brain has not produced a speakable sentence shortly after the
transcript is known, the gateway speaks one short cached phrase first, then the
answer. The acknowledgement is an ordinary ``llm`` + ``tts sentence_start`` in
the same turn: exactly one ``tts start`` and one ``tts stop``, and the answer's
own ``llm`` carries the chosen emotion.

The acknowledgement comes *after* STT, not before: FR-006 makes an empty
transcript end the turn with no further messages, so the gateway must not speak
into a silence or a noise burst it has not transcribed yet.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

AUDIO = Path(__file__).resolve().parents[1] / "fixtures" / "audio"
ACK = "Right."
ANSWER = "It is nine fifteen in the evening."
SECOND = " The evening is young."


async def _speak(device) -> None:
    await device.listen("start")
    await device.stream_wav(AUDIO / "utterance_ok.wav")
    await device.listen("stop")


def _sentence_starts(device) -> list[str]:
    return [
        m["text"]
        for m in device.log.after_hello()
        if m.get("type") == "tts" and m.get("state") == "sentence_start"
    ]


def _llms(device) -> list[dict]:
    return [m for m in device.log.after_hello() if m.get("type") == "llm"]


async def test_a_slow_brain_is_acknowledged_before_the_answer(harness_factory) -> None:
    harness = await harness_factory(JG_ACK_DELAY_MS="150")
    harness.hermes.pacing_s = 1.0
    harness.hermes.tokens = ["It is nine", " fifteen in the evening.", SECOND]
    device = await harness.connect()
    try:
        await _speak(device)

        # The acknowledgement is audible while the answer is still being generated.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 1.0
        while not device.log.received_audio and loop.time() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.01)
        assert device.log.received_audio, "nothing was audible before the answer"
        assert _sentence_starts(device) == [ACK], "the answer started before the acknowledgement"

        await device.wait_for_tts_stop(15)
        messages = device.log.after_hello()
    finally:
        await device.close()

    assert [m["type"] for m in messages[:3]] == ["stt", "llm", "tts"]
    assert messages[1]["text"] == ACK
    assert messages[2]["state"] == "start"
    assert _sentence_starts(device) == [ACK, ANSWER, SECOND.strip()]
    assert [m["text"] for m in _llms(device)] == [ACK, ANSWER]
    assert _llms(device)[0]["emotion"] in {
        "neutral",
        "happy",
        "laughing",
        "angry",
        "sad",
        "crying",
        "sleepy",
        "doubtful",
    }
    assert [m["state"] for m in messages if m.get("state") in {"start", "stop"}].count("start") == 1
    assert [m["state"] for m in messages if m.get("state") in {"start", "stop"}].count("stop") == 1


async def test_zero_ack_text_disables_the_acknowledgement(harness_factory) -> None:
    """Negative control: the same slow brain, with the phrase switched off."""

    harness = await harness_factory(JG_ACK_DELAY_MS="150", JG_ACK_TEXT="")
    harness.hermes.pacing_s = 1.0
    harness.hermes.tokens = ["It is nine", " fifteen in the evening.", SECOND]
    device = await harness.connect()
    try:
        await _speak(device)
        await device.wait_for_tts_stop(15)
        starts = _sentence_starts(device)
    finally:
        await device.close()

    assert starts == [ANSWER, SECOND.strip()]
    assert [m["text"] for m in _llms(device)] == [ANSWER]


async def test_a_fast_brain_is_not_acknowledged(harness_factory) -> None:
    harness = await harness_factory(JG_ACK_DELAY_MS="300")
    harness.hermes.pacing_s = 0.0
    harness.hermes.tokens = ["It is nine", " fifteen in the evening.", SECOND]
    device = await harness.connect()
    try:
        await _speak(device)
        await device.wait_for_tts_stop(15)
        starts = _sentence_starts(device)
    finally:
        await device.close()

    assert starts == [ANSWER, SECOND.strip()]
    assert [m["text"] for m in _llms(device)] == [ANSWER]


async def test_an_empty_transcript_never_speaks_the_acknowledgement(harness_factory) -> None:
    harness = await harness_factory(JG_ACK_DELAY_MS="10")
    harness.whisper.mode = "empty"
    device = await harness.connect()
    try:
        await _speak(device)
        assert await device.wait_quiet(0.5) is True
    finally:
        await device.close()

    assert device.log.after_hello() == []
    assert harness.tts.calls.count(ACK) <= 1  # at most the one-off cache warm-up
    assert device.log.received_audio == []


async def test_the_acknowledgement_is_synthesised_once(harness_factory) -> None:
    harness = await harness_factory(JG_ACK_DELAY_MS="20")
    harness.hermes.pacing_s = 0.3
    harness.hermes.tokens = ["It is nine", " fifteen in the evening.", SECOND]
    device = await harness.connect()
    try:
        for _ in range(2):
            await _speak(device)
            await device.wait_for_tts_stop(15)
            assert await device.wait_quiet(0.1)
    finally:
        await device.close()

    assert harness.tts.calls.count(ACK) == 1
