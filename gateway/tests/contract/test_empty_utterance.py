"""US1 edge cases: nothing to transcribe means nothing is said (T019).

Silence, a 100 ms noise burst, a ``listen stop`` with no audio and an utterance
that hits the hard cap must all end in the same defined way: no ``stt``, no
``llm``, no ``tts``, no binary frame — and a session that is immediately usable
for the next question (FR-005, FR-006, FR-018, acceptance scenario 5).
"""

from __future__ import annotations

from pathlib import Path

from jarvis_gateway.audio import UPLINK_RATE, OpusEncoder, frame_60ms
from tools.fake_device import read_wav_16k

AUDIO = Path(__file__).resolve().parents[1] / "fixtures" / "audio"


def opus_packets(name: str, *, limit: int | None = None) -> list[bytes]:
    pcm, _ = read_wav_16k(AUDIO / name)
    frames = frame_60ms(pcm, UPLINK_RATE)
    if limit is not None:
        frames = frames[:limit]
    encoder = OpusEncoder(UPLINK_RATE)
    try:
        return [encoder.encode(frame) for frame in frames]
    finally:
        encoder.close()


async def _assert_nothing_was_said(device) -> None:
    assert await device.wait_quiet(0.4) is True
    assert device.log.after_hello() == []
    assert device.log.received_audio == []


async def test_silence_produces_no_turn_and_the_session_keeps_working(harness) -> None:
    device = await harness.connect()
    try:
        await device.listen("start")
        await device.send_packets(opus_packets("silence_2s.wav"))
        await device.listen("stop")
        await _assert_nothing_was_said(device)

        # The same session accepts the next question.
        harness.whisper.text = "what time is it"
        await device.listen("start")
        await device.stream_wav(AUDIO / "utterance_ok.wav")
        await device.listen("stop")
        stop = await device.wait_for(lambda m: m.get("state") == "stop", timeout=10)

        assert stop["type"] == "tts"
        assert [m["type"] for m in device.log.after_hello()][:2] == ["stt", "llm"]
    finally:
        await device.close()


async def test_a_hundred_millisecond_noise_burst_is_too_short(harness) -> None:
    device = await harness.connect()
    try:
        await device.listen("start")
        await device.send_packets(opus_packets("noise_burst_100ms.wav"))
        await device.listen("stop")
        await _assert_nothing_was_said(device)
    finally:
        await device.close()
    assert harness.whisper.requests == []


async def test_listen_stop_with_no_audio_says_nothing(harness) -> None:
    device = await harness.connect()
    try:
        await device.listen("start")
        await device.listen("stop")
        await _assert_nothing_was_said(device)
    finally:
        await device.close()


async def test_a_forced_end_of_noise_still_says_nothing(harness_factory) -> None:
    """The 30 s cap closes the utterance; with no words in it, nothing is said.

    The cap is shortened to 1 s so the test is not a 30 s wait; the path is the
    same ``forced=True`` branch of the VAD.
    """

    harness = await harness_factory(JG_MAX_UTTERANCE_S="1.0", JG_VAD_MIN_SPEECH_MS="250")
    harness.whisper.mode = "empty"
    device = await harness.connect()
    try:
        await device.listen("start")
        # ~1.5 s of above-threshold audio, no stop: the cap ends the utterance.
        await device.stream_wav(AUDIO / "utterance_ok.wav")
        await _assert_nothing_was_said(device)
        # A speechless forced end leaves the session listening and usable.
        harness.whisper.mode = "ok"
        harness.whisper.text = "nine fifteen"
        await device.listen("start")
        await device.stream_wav(AUDIO / "utterance_ok.wav", count=20)
        await device.listen("stop")
        await device.wait_for(lambda m: m.get("state") == "stop", timeout=10)
        assert device.log.after_hello()[0]["type"] == "stt"
    finally:
        await device.close()


async def test_a_forced_end_with_real_words_still_answers(harness_factory) -> None:
    """The cap must not swallow a real question that ran long (FR-005)."""

    harness = await harness_factory(JG_MAX_UTTERANCE_S="1.0")
    device = await harness.connect()
    try:
        await device.listen("start")
        await device.stream_wav(AUDIO / "utterance_ok.wav")
        stop = await device.wait_for(lambda m: m.get("state") == "stop", timeout=10)

        assert stop["type"] == "tts"
        assert [m["type"] for m in device.log.after_hello()][:2] == ["stt", "llm"]
    finally:
        await device.close()
