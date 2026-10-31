"""US1 contract: a spoken question is answered aloud, in order (T017).

The turn is driven over a real socket by the synthetic device; whisper, Hermes
and edge-tts are stand-ins. What the firmware would see is asserted exactly:
``hello`` → ``stt`` → ``llm`` (emotion from the firmware set) → ``tts start`` →
``tts sentence_start`` → binary frames → one ``tts stop``, one session id
throughout, and silence after the stop until the next turn.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.fake_device import SequenceMismatch, check_loop_sequence

AUDIO = Path(__file__).resolve().parents[1] / "fixtures" / "audio"


async def test_the_full_turn_sequence_is_what_the_firmware_expects(harness) -> None:
    harness.whisper.text = "what time is it"
    harness.hermes.tokens = ["It is", " nine", " fifteen in the evening."]
    device = await harness.connect()
    try:
        await device.listen("start")
        sent = await device.stream_wav(AUDIO / "utterance_ok.wav")
        await device.listen("stop")
        stop = await device.wait_for(
            lambda m: m.get("type") == "tts" and m.get("state") == "stop", timeout=10
        )
        quiet = await device.wait_quiet(0.2)

        messages = device.log.after_hello()
        check_loop_sequence(
            messages,
            audio_frames=len(device.log.received_audio),
            session_id=stop["session_id"],
            quiet=quiet,
        )
    finally:
        await device.close()

    assert sent == 25  # 1.5 s of 60 ms frames
    assert harness.whisper.requests, "the utterance was never uploaded"
    assert harness.hermes.requests[0]["body"]["messages"] == [
        {"role": "user", "content": "what time is it"}
    ]
    # Every JSON frame carries the one session id issued in the server hello.
    assert {m["session_id"] for m in messages} == {device.log.session_id}
    # The first sentence was spoken before the brain's last delta was consumed.
    assert harness.hermes.requests[0]["streamed_tokens"] == 3


async def test_a_second_sentence_gets_its_own_sentence_start(harness) -> None:
    harness.hermes.tokens = [
        "The first sentence is right here.",
        " And this is the second sentence, spoken after it.",
    ]
    device = await harness.connect()
    try:
        await device.listen("start")
        await device.stream_wav(AUDIO / "utterance_ok.wav")
        await device.listen("stop")
        await device.wait_for(lambda m: m.get("state") == "stop", timeout=10)

        messages = device.log.after_hello()
    finally:
        await device.close()

    starts = [m["text"] for m in messages if m.get("state") == "sentence_start"]
    assert starts == [
        "The first sentence is right here.",
        "And this is the second sentence, spoken after it.",
    ]
    # One llm, one tts start, one stop for the whole turn.
    assert [m["type"] for m in messages].count("llm") == 1
    assert [m.get("state") for m in messages if m.get("type") == "tts"].count("start") == 1
    assert [m.get("state") for m in messages if m.get("type") == "tts"].count("stop") == 1


async def test_a_broken_sequence_is_caught_by_the_assertion(harness) -> None:
    """Negative control: the same helper rejects a turn with no llm frame."""

    harness.hermes.tokens = ["One complete sentence, long enough."]
    device = await harness.connect()
    try:
        await device.listen("start")
        await device.stream_wav(AUDIO / "utterance_ok.wav")
        await device.listen("stop")
        await device.wait_for(lambda m: m.get("state") == "stop", timeout=10)
        messages = [
            message for message in device.log.after_hello() if message.get("type") != "llm"
        ]
    finally:
        await device.close()

    with pytest.raises(SequenceMismatch):
        check_loop_sequence(
            messages,
            audio_frames=len(device.log.received_audio),
            session_id=device.log.session_id,
        )
