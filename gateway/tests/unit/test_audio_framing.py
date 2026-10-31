"""Audio framing and resampling (T010).

The device plays one Opus packet per WebSocket binary frame. Every packet must
be exactly 60 ms: 1440 samples at 24 kHz downlink, 960 at 16 kHz uplink. The
final frame of a sentence is padded with silence — never shortened and never
dropped — because a short packet would change the frame duration on the wire.
"""

from __future__ import annotations

import array

import pytest

from jarvis_gateway import audio


def pcm(sample_count: int, seed: int = 0) -> bytes:
    """Deterministic PCM16 with a value per sample, so drops and shifts are visible."""

    return array.array(
        "h", [(index * 7 + seed) % 2000 - 1000 for index in range(sample_count)]
    ).tobytes()


def test_frame_sizes_are_exactly_sixty_milliseconds() -> None:
    frames_24k = audio.frame_60ms(bytes(2880 * 3), 24000)
    frames_16k = audio.frame_60ms(bytes(1920 * 3), 16000)

    assert [len(frame) for frame in frames_24k] == [2880] * 3
    assert [len(frame) for frame in frames_16k] == [1920] * 3


def test_frame_60ms_keeps_every_sample_and_pads_only_the_tail() -> None:
    source = pcm(1440 * 3 + 500)

    frames = audio.frame_60ms(source, 24000)

    assert len(frames) == 4
    assert all(len(frame) == 2880 for frame in frames)
    padding = len(frames[-1]) - (len(source) - 2880 * 3)
    assert 0 < padding < 2880
    assert b"".join(frames)[: len(source)] == source
    assert b"".join(frames)[len(source) :] == bytes(padding)


def test_exact_multiple_does_not_add_an_empty_frame() -> None:
    frames = audio.frame_60ms(bytes(2880 * 2), 24000)

    assert len(frames) == 2


def test_empty_audio_frames_to_nothing() -> None:
    assert audio.frame_60ms(b"", 24000) == []


def test_unknown_frame_rate_is_refused() -> None:
    with pytest.raises(audio.AudioError):
        audio.frame_60ms(bytes(100), 44100)


def test_resample_16k_to_24k_preserves_duration() -> None:
    source = audio.frame_60ms(pcm(960), 16000)[0]

    converted = audio.pcm16_16k_to_24k(source)

    assert audio.pcm16_samples(converted) == 1440
    assert len(converted) == 2880


def test_resample_16k_to_24k_over_a_stream_has_no_drift() -> None:
    source = b"".join(audio.frame_60ms(pcm(960 * 4), 16000))

    converted = audio.pcm16_16k_to_24k(source)

    assert audio.pcm16_samples(converted) == 1440 * 4


def test_downlink_frames_encode_to_packets_that_decode_as_sixty_ms() -> None:
    encoder = audio.OpusEncoder()
    decoder = audio.OpusDecoder(sample_rate=audio.DOWNLINK_RATE)
    upsampled = audio.pcm16_16k_to_24k(bytes(1920 * 2))
    frames = audio.frame_60ms(upsampled, 24000)

    packets = [encoder.encode(frame) for frame in frames]
    decoded = b"".join(decoder.decode(packet) for packet in packets)

    assert len(packets) == 2
    assert audio.pcm16_samples(decoded) == 1440 * 2


def test_uplink_encoder_accepts_16k_frames_and_decodes_back() -> None:
    encoder = audio.OpusEncoder(sample_rate=audio.UPLINK_RATE)
    decoder = audio.OpusDecoder(sample_rate=audio.UPLINK_RATE)
    frames = audio.frame_60ms(pcm(960), 16000)

    packet = encoder.encode(frames[0])

    assert audio.pcm16_samples(decoder.decode(packet)) == 960


def test_pcm16_helpers_agree() -> None:
    data = pcm(17)

    assert audio.pcm16_samples(data) == 17
    assert audio.pcm16_silence(3) == bytes(6)
    assert audio.pcm16_concat([data[:4], data[4:]]) == data


def test_pcm16_rejects_odd_length() -> None:
    with pytest.raises(audio.AudioError):
        audio.pcm16_samples(b"\x00")
