"""Codec contract on CT 914 (T009).

The ten ``opus_16k_60ms`` packets are real SILK-WB packets produced by libopus
(see ``tests/fixtures/make_audio.py``). Opus decodes natively at 48 kHz, so the
decoder resamples to the protocol's uplink rate (16 kHz); one packet therefore
yields exactly 960 samples of 16 kHz PCM16.

The round-trip test encodes 24 kHz PCM, decodes it back through the same codec
API and compares sample-for-sample. Opus has a 6.5 ms look-ahead (312 samples at
48 kHz, 156 at 24 kHz), so the decoded stream leads the source by
``CODEC_DELAY_SAMPLES``; that shift is a property of the codec, documented here
and compensated in the measurement rather than hidden.
"""

from __future__ import annotations

import array
import math
from pathlib import Path

import pytest

from jarvis_gateway import audio

OPUS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "audio" / "opus_16k_60ms"


def sine(samples: int, rate: int = 24000, freq: float = 440.0, amplitude: int = 8000) -> bytes:
    return array.array(
        "h",
        [int(amplitude * math.sin(2 * math.pi * freq * i / rate)) for i in range(samples)],
    ).tobytes()


def snr_db(reference: bytes, measured: bytes, skip: int) -> float:
    """Signal-to-noise ratio of ``measured`` against ``reference``.

    ``measured`` leads by ``skip`` samples (the codec look-ahead), so the
    comparison starts ``skip`` samples into the measured stream.
    """

    ref = array.array("h")
    ref.frombytes(reference)
    got = array.array("h")
    got.frombytes(measured[skip * 2 :])
    pairs = list(zip(ref, got, strict=False))
    signal = sum(a * a for a, _ in pairs)
    noise = sum((a - b) ** 2 for a, b in pairs)
    assert noise, "identical streams would make the SNR assertion vacuous"
    return 10.0 * math.log10(signal / noise)


def test_codec_module_reports_the_protocol_rates() -> None:
    assert audio.UPLINK_RATE == 16000
    assert audio.DOWNLINK_RATE == 24000
    assert audio.FRAME_MS == 60
    assert audio.UPLINK_FRAME_SAMPLES == 960
    assert audio.DOWNLINK_FRAME_SAMPLES == 1440


def test_decodes_each_fixture_packet_to_exactly_60ms() -> None:
    decoder = audio.OpusDecoder()

    decoded = [decoder.decode(path.read_bytes()) for path in sorted(OPUS_DIR.glob("*.bin"))]

    assert [audio.pcm16_samples(chunk) for chunk in decoded] == [960] * 10
    assert audio.pcm16_samples(b"".join(decoded)) == 9600


def test_decoded_samples_are_pcm16_and_not_silent() -> None:
    decoder = audio.OpusDecoder()

    pcm = b"".join(
        decoder.decode(path.read_bytes()) for path in sorted(OPUS_DIR.glob("*.bin"))
    )

    samples = array.array("h")
    samples.frombytes(pcm)
    assert max(abs(sample) for sample in samples) > 1000


def test_encoder_round_trip_keeps_the_sample_count() -> None:
    source = sine(1440 * 10)
    encoder = audio.OpusEncoder()
    frames = [source[i * 2880 : (i + 1) * 2880] for i in range(10)]

    packets = [encoder.encode(frame) for frame in frames]

    assert all(len(packet) > 5 for packet in packets)
    decoder = audio.OpusDecoder(sample_rate=audio.DOWNLINK_RATE)
    decoded = b"".join(decoder.decode(packet) for packet in packets)
    assert audio.pcm16_samples(decoded) == audio.pcm16_samples(source)


def test_encoder_round_trip_snr_is_above_the_documented_threshold() -> None:
    source = sine(1440 * 10)
    encoder = audio.OpusEncoder()
    packets = [
        encoder.encode(source[i * 2880 : (i + 1) * 2880]) for i in range(10)
    ]

    decoder = audio.OpusDecoder(sample_rate=audio.DOWNLINK_RATE)
    decoded = b"".join(decoder.decode(packet) for packet in packets)

    measured = snr_db(source, decoded, skip=audio.CODEC_DELAY_SAMPLES)
    assert measured >= 20.0, f"round-trip SNR {measured:.1f} dB below the documented 20 dB"


def test_encoder_refuses_a_partial_frame() -> None:
    encoder = audio.OpusEncoder()

    with pytest.raises(audio.AudioError):
        encoder.encode(bytes(1000))


def test_decoder_rejects_garbage() -> None:
    decoder = audio.OpusDecoder()

    with pytest.raises(audio.AudioError):
        decoder.decode(b"\xff")
