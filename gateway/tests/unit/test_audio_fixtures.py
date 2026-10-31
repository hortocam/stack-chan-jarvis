"""Audio fixture hygiene (T003).

The VAD tests (T012) and the codec tests (T009) are written against these
fixtures, so their shape is pinned here: rate, channel count, sample width,
duration, and ten non-empty 60 ms Opus packets. ``long_35s.wav`` is generated
on demand (git-ignored) and therefore only checked when it is present.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

AUDIO_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "audio"
OPUS_DIR = AUDIO_DIR / "opus_16k_60ms"

WAV_FIXTURES = {
    "utterance_ok.wav": 1.5,
    "silence_2s.wav": 2.0,
    "noise_burst_100ms.wav": 2.0,
}


def read_wav(path: Path) -> tuple[wave._wave_params, bytes]:
    with wave.open(str(path), "rb") as handle:
        return handle.getparams(), handle.readframes(handle.getnframes())


# RFC 6716 table 2: config -> frame duration in ms.
_FRAME_MS = [
    10, 20, 40, 60,          # 0-3   SILK NB
    10, 20, 40, 60,          # 4-7   SILK MB
    10, 20, 40, 60,          # 8-11  SILK WB
    10, 20,                  # 12-13 Hybrid SWB
    10, 20,                  # 14-15 Hybrid FB
    2.5, 5, 10, 20,          # 16-19 CELT NB
    2.5, 5, 10, 20,          # 20-23 CELT WB
    2.5, 5, 10, 20,          # 24-27 CELT SWB
    2.5, 5, 10, 20,          # 28-31 CELT FB
]


def opus_packet_duration_ms(payload: bytes) -> float:
    """Total duration of one Opus packet, from its TOC byte (RFC 6716 §3.1)."""
    toc = payload[0]
    config = toc >> 3
    code = toc & 0b11
    frame_ms = _FRAME_MS[config]

    if code in (0, 1):
        frames = 1 if code == 0 else 2
    elif code == 2:
        frames = 2
    else:
        frames = payload[1] & 0b0011_1111

    return frames * frame_ms


@pytest.mark.parametrize(("name", "seconds"), sorted(WAV_FIXTURES.items()))
def test_wav_fixture_is_16k_mono_pcm16(name: str, seconds: float) -> None:
    params, frames = read_wav(AUDIO_DIR / name)

    assert params.nchannels == 1
    assert params.sampwidth == 2
    assert params.framerate == 16_000
    assert params.nframes == int(seconds * 16_000)
    assert len(frames) == params.nframes * 2


def peak_between(frames: bytes, start_ms: int, end_ms: int) -> int:
    """Largest absolute sample value in a millisecond window of 16 kHz PCM16."""
    window = frames[start_ms * 32 : end_ms * 32]
    samples = (
        int.from_bytes(window[i : i + 2], "little", signed=True)
        for i in range(0, len(window) - 1, 2)
    )
    return max((abs(sample) for sample in samples), default=0)


def test_silence_fixture_has_no_energy() -> None:
    _, frames = read_wav(AUDIO_DIR / "silence_2s.wav")

    assert set(frames) == {0}


def test_utterance_fixture_has_speech_level_energy() -> None:
    _, frames = read_wav(AUDIO_DIR / "utterance_ok.wav")

    assert peak_between(frames, 100, 1400) > 3_000
    # The clip must start and end quietly so a VAD has a clean edge to find.
    assert frames[: 2 * 800] == bytes(2 * 800)


def test_noise_burst_fixture_is_bursty_not_continuous() -> None:
    _, frames = read_wav(AUDIO_DIR / "noise_burst_100ms.wav")

    assert peak_between(frames, 950, 1050) > 3_000
    assert peak_between(frames, 100, 900) == 0
    assert peak_between(frames, 1100, 1900) == 0


def test_long_fixture_is_generated_on_demand() -> None:
    path = AUDIO_DIR / "long_35s.wav"
    if not path.is_file():
        pytest.skip("long_35s.wav is generated: run tests/fixtures/make_audio.py")

    params, _ = read_wav(path)
    assert params.framerate == 16_000
    assert params.nframes / params.framerate >= 35.0


def test_ten_opus_packets_exist_and_are_not_empty() -> None:
    packets = sorted(OPUS_DIR.glob("*.bin"))

    assert [p.name for p in packets] == [f"{index:03d}.bin" for index in range(1, 11)]
    sizes = [p.stat().st_size for p in packets]
    assert min(sizes) > 10
    assert max(sizes) < 400


@pytest.mark.parametrize("name", [f"{index:03d}.bin" for index in range(1, 11)])
def test_opus_packet_is_one_sixty_millisecond_frame(name: str) -> None:
    payload = (OPUS_DIR / name).read_bytes()

    assert opus_packet_duration_ms(payload) == 60.0
