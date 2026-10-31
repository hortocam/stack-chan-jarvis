"""Energy VAD contract (T012), driven by the T003 WAV fixtures.

Frame geometry: 60 ms at 16 kHz is 960 samples (1920 bytes).

Index arithmetic for ``utterance_ok.wav`` (0.1 s silence, 1.3 s speech, 0.1 s
silence):
    frame 0   [0, 60)      silence
    frame 1   [60, 120)    20 ms speech -> voiced, first of the two that start
    frames 2..23           speech
    frame 24  [1440, 1500) silence, the first frame after the utterance
So the utterance runs from frame 1 to frame 23 inclusive: 23 voiced frames,
1380 ms. The file's 100 ms tail is shorter than the default 300 ms silence
window, so the test appends silence to let the detector close the utterance on
its own silence rule.
"""

from __future__ import annotations

import array
import math
import wave
from pathlib import Path

import pytest

from jarvis_gateway.vad import Vad, VadConfig, frame_rms

AUDIO_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "audio"
FRAME_BYTES = 960 * 2


def read_frames(name: str) -> list[bytes]:
    with wave.open(str(AUDIO_DIR / name), "rb") as handle:
        data = handle.readframes(handle.getnframes())
    frames = [data[i : i + FRAME_BYTES] for i in range(0, len(data), FRAME_BYTES)]
    if frames and len(frames[-1]) < FRAME_BYTES:
        frames[-1] = frames[-1] + bytes(FRAME_BYTES - len(frames[-1]))
    return frames


def silence_frames(count: int) -> list[bytes]:
    return [bytes(FRAME_BYTES)] * count


def tone_frame(amplitude: int, freq: float = 180.0) -> bytes:
    return array.array(
        "h",
        [
            int(amplitude * math.sin(2 * math.pi * freq * i / 16000))
            for i in range(960)
        ],
    ).tobytes()


def feed(vad: Vad, frames: list[bytes]) -> list:
    results = []
    for frame in frames:
        result = vad.push(frame)
        if result is not None:
            results.append(result)
    return results


def test_rms_is_computed_over_pcm16() -> None:
    assert frame_rms(bytes(FRAME_BYTES)) == 0.0
    assert frame_rms(tone_frame(8000)) > 4000


def test_silence_yields_no_utterance() -> None:
    vad = Vad()

    assert feed(vad, read_frames("silence_2s.wav")) == []
    assert vad.finish() is None
    assert vad.speaking is False


def test_hundred_millisecond_burst_is_too_short() -> None:
    vad = Vad()

    results = feed(vad, read_frames("noise_burst_100ms.wav"))
    final = results[-1] if results else vad.finish()

    assert final is not None
    assert final.too_short is True
    assert final.voiced_ms == 180  # three 60 ms frames carry part of the burst
    assert final.pcm == b""


def test_normal_speech_starts_and_ends_at_the_expected_frames() -> None:
    vad = Vad()

    results = feed(vad, read_frames("utterance_ok.wav") + silence_frames(6))

    assert len(results) == 1
    utterance = results[0]
    assert utterance.start_frame == 1
    assert utterance.end_frame == 24
    assert utterance.reason == "silence_for_detected"
    assert utterance.voiced_ms == 1380
    assert utterance.forced is False
    assert utterance.too_short is False
    assert utterance.pcm == b"".join(read_frames("utterance_ok.wav")[1:24])


def test_stream_end_closes_an_utterance_with_the_stop_reason() -> None:
    vad = Vad()

    assert feed(vad, read_frames("utterance_ok.wav")) == []
    utterance = vad.finish("stop")

    assert utterance is not None
    assert utterance.reason == "stop"
    assert utterance.start_frame == 1
    assert utterance.end_frame == 25
    assert utterance.too_short is False


def test_long_utterance_is_forced_at_the_cap() -> None:
    vad = Vad(max_utterance_s=30.0)
    continuous = [tone_frame(8000)] * 40 * 13  # 31.2 s of continuous speech

    results = feed(vad, continuous)

    assert len(results) == 1
    utterance = results[0]
    assert utterance.forced is True
    assert utterance.reason == "max_duration"
    assert utterance.end_frame == 500  # 500 frames * 60 ms = 30.0 s
    assert len(utterance.pcm) == 500 * FRAME_BYTES


def test_silence_window_comes_from_config() -> None:
    speech = [tone_frame(8000)] * 40  # 2.4 s, then a pause
    frames = speech + silence_frames(4)
    default = Vad()
    short = Vad(silence_ms=120)

    default_results = feed(default, frames)
    short_results = feed(short, frames)

    assert default_results == []
    assert len(short_results) == 1
    assert short_results[0].end_frame == 40  # speech occupies frames 0..39
    assert short_results[0].reason == "silence_for_detected"


def test_min_speech_comes_from_config() -> None:
    frames = read_frames("noise_burst_100ms.wav")

    strict = Vad(min_speech_ms=250)
    lenient = Vad(min_speech_ms=150)

    strict_result = feed(strict, frames) or [strict.finish()]
    lenient_result = feed(lenient, frames) or [lenient.finish()]

    assert strict_result[-1].too_short is True
    assert lenient_result[-1].too_short is False
    assert lenient_result[-1].pcm != b""


def test_two_voiced_frames_are_required_to_start() -> None:
    vad = Vad()

    assert vad.push(tone_frame(8000)) is None
    assert vad.speaking is False
    assert vad.push(tone_frame(8000)) is None
    assert vad.speaking is True


def test_hysteresis_keeps_a_quiet_but_voiced_frame_inside_the_utterance() -> None:
    quiet = array.array("h", [600] * 960).tobytes()
    vad = Vad(start_rms=1000.0, continue_rms=500.0, min_speech_ms=0)

    feed(vad, [tone_frame(8000), tone_frame(8000), quiet])
    results = feed(vad, silence_frames(5))

    assert len(results) == 1
    assert results[0].end_frame == 3  # the 600-RMS frame is not silence
    assert results[0].pcm.endswith(quiet)


def test_a_frame_below_the_start_threshold_never_opens_an_utterance() -> None:
    vad = Vad()

    feed(vad, [tone_frame(500)] * 10)

    assert vad.speaking is False
    assert vad.finish() is None


def test_utterance_pcm_is_capped_by_the_maximum() -> None:
    vad = Vad(max_utterance_s=1.0)

    results = feed(vad, [tone_frame(8000)] * 30)

    assert len(results) == 1
    assert results[0].end_frame == 17  # the 17th 60 ms frame crosses 1.0 s
    assert len(results[0].pcm) == 17 * FRAME_BYTES


@pytest.mark.parametrize("bad", [0, -1])
def test_invalid_silence_window_is_rejected(bad: int) -> None:
    with pytest.raises(ValueError):
        VadConfig(silence_ms=bad)
