"""Opus codec, resampling and 60 ms framing (T009, T010).

One WebSocket binary frame is one Opus packet and one packet is exactly 60 ms:
960 samples at the 16 kHz uplink, 1440 at the 24 kHz downlink (plan C2).

PyAV (``av``) is the codec layer (plan D2). It bundles libopus and swresample,
so the gateway needs no system codec build. Opus itself always decodes at
48 kHz; the decoder here resamples to the protocol rate, and a fresh resampler
is used per packet so its filter delay is flushed inside that packet instead of
accumulating. That makes ``decode(packet)`` return exactly 960 (or 1440)
samples every time, which is what the VAD's frame arithmetic and the tests
assume.

Opus has a 6.5 ms look-ahead. An encode/decode round trip is therefore shifted
by ``CODEC_DELAY_SAMPLES`` (156 samples at 24 kHz); the SNR test in
``tests/unit/test_audio_codec.py`` compensates for that documented delay rather
than pretending the codec is sample-aligned.
"""

from __future__ import annotations

from typing import Any

import av

__all__ = [
    "AudioError",
    "CODEC_DELAY_MS",
    "CODEC_DELAY_SAMPLES",
    "DOWNLINK_FRAME_SAMPLES",
    "DOWNLINK_RATE",
    "FRAME_MS",
    "OpusDecoder",
    "OpusEncoder",
    "SUPPORTED_RATES",
    "UPLINK_FRAME_SAMPLES",
    "UPLINK_RATE",
    "frame_60ms",
    "pcm16_concat",
    "pcm16_16k_to_24k",
    "pcm16_samples",
    "pcm16_silence",
    "resample_pcm16",
]

UPLINK_RATE = 16000
DOWNLINK_RATE = 24000
FRAME_MS = 60
SUPPORTED_RATES = frozenset({UPLINK_RATE, DOWNLINK_RATE})

UPLINK_FRAME_SAMPLES = UPLINK_RATE * FRAME_MS // 1000  # 960
DOWNLINK_FRAME_SAMPLES = DOWNLINK_RATE * FRAME_MS // 1000  # 1440

# libopus look-ahead: 312 samples at 48 kHz = 6.5 ms = 156 samples at 24 kHz.
CODEC_DELAY_MS = 6.5
CODEC_DELAY_SAMPLES = 156


class AudioError(RuntimeError):
    """An audio packet, frame or buffer that violates the 60 ms contract."""


def _frame_bytes(frame: Any) -> bytes:
    """PCM bytes of one decoded frame, without the decoder's buffer padding."""

    channels = len(frame.layout.channels)
    return bytes(memoryview(frame.planes[0])[: frame.samples * channels * 2])


def _check_rate(rate: int) -> int:
    if rate not in SUPPORTED_RATES:
        raise AudioError(
            f"unsupported sample rate {rate}; expected one of {sorted(SUPPORTED_RATES)}"
        )
    return rate


def _resample_frames(frames: list[Any], rate: int) -> bytes:
    """Resample decoded 48 kHz frames to ``rate`` as mono PCM16, flushing the tail."""

    resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
    out = bytearray()
    for frame in frames:
        for chunk in resampler.resample(frame):
            out += _frame_bytes(chunk)
    for chunk in resampler.resample(None):
        out += _frame_bytes(chunk)
    return bytes(out)


def pcm16_samples(data: bytes | bytearray) -> int:
    """Number of PCM16 samples in ``data`` (two bytes each)."""

    if len(data) % 2:
        raise AudioError(f"PCM16 buffers must have an even length, got {len(data)}")
    return len(data) // 2


def pcm16_silence(samples: int) -> bytes:
    if samples < 0:
        raise AudioError("sample count must not be negative")
    return bytes(samples * 2)


def pcm16_concat(parts: list[bytes]) -> bytes:
    return b"".join(parts)


def frame_60ms(pcm16: bytes, sample_rate: int) -> list[bytes]:
    """Split mono PCM16 into 60 ms frames, padding only the final frame.

    No samples are dropped and no empty frame is appended when the input is an
    exact multiple: a short packet would change the wire frame duration.
    """

    _check_rate(sample_rate)
    pcm16_samples(pcm16)  # length check
    if not pcm16:
        return []

    frame_bytes = sample_rate * FRAME_MS // 1000 * 2
    frames = [pcm16[i : i + frame_bytes] for i in range(0, len(pcm16), frame_bytes)]
    tail = frames[-1]
    if len(tail) < frame_bytes:
        frames[-1] = tail + bytes(frame_bytes - len(tail))
    return frames


def resample_pcm16(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample mono PCM16 between the two protocol rates, preserving duration."""

    _check_rate(src_rate)
    _check_rate(dst_rate)
    pcm16_samples(data)
    if not data:
        return b""
    if src_rate == dst_rate:
        return data
    resampler = av.AudioResampler(format="s16", layout="mono", rate=dst_rate)
    frame = av.AudioFrame(format="s16", layout="mono", samples=len(data) // 2)
    frame.sample_rate = src_rate
    frame.planes[0].update(data)
    out = bytearray()
    for chunk in resampler.resample(frame):
        out += _frame_bytes(chunk)
    for chunk in resampler.resample(None):
        out += _frame_bytes(chunk)
    return bytes(out)


def pcm16_16k_to_24k(data: bytes) -> bytes:
    """Uplink-rate PCM to downlink-rate PCM (duration preserved: 2:3)."""

    return resample_pcm16(data, UPLINK_RATE, DOWNLINK_RATE)


class OpusDecoder:
    """Decode one 60 ms Opus packet to mono PCM16 at ``sample_rate``."""

    def __init__(self, sample_rate: int = UPLINK_RATE) -> None:
        self.sample_rate = _check_rate(sample_rate)
        self._context = av.CodecContext.create("libopus", "r")

    def decode(self, packet: bytes) -> bytes:
        if not isinstance(packet, (bytes, bytearray)) or not packet:
            raise AudioError("Opus packets must be non-empty bytes")
        try:
            frames = self._context.decode(av.Packet(bytes(packet)))
        except (EOFError, ValueError, av.FFmpegError) as exc:
            raise AudioError(f"invalid Opus packet: {exc}") from exc
        if not frames:
            raise AudioError("Opus packet produced no audio")
        return _resample_frames(frames, self.sample_rate)

    def close(self) -> None:
        """Release the codec context (idempotent; the GC also does this)."""


class OpusEncoder:
    """Encode one 60 ms mono PCM16 frame to one Opus packet.

    ``frame_duration=60`` is libopus' own option (not a PyAV default, which is
    20 ms); without it one 1440-sample frame becomes three packets and the
    device would see three 20 ms frames where the protocol promises one 60 ms
    frame.
    """

    def __init__(self, sample_rate: int = DOWNLINK_RATE, bit_rate: int = 32000) -> None:
        self.sample_rate = _check_rate(sample_rate)
        self.frame_samples = self.sample_rate * FRAME_MS // 1000
        self._context = av.CodecContext.create("libopus", "w")
        self._context.sample_rate = self.sample_rate
        self._context.format = "s16"
        self._context.layout = "mono"
        self._context.bit_rate = bit_rate
        self._context.options = {"frame_duration": str(FRAME_MS)}
        self._context.open()

    def encode(self, pcm16: bytes) -> bytes:
        samples = pcm16_samples(pcm16)
        if samples != self.frame_samples:
            raise AudioError(
                f"expected a {FRAME_MS} ms frame ({self.frame_samples} samples), got {samples}"
            )
        frame = av.AudioFrame(format="s16", layout="mono", samples=samples)
        frame.sample_rate = self.sample_rate
        frame.planes[0].update(pcm16)
        packets = self._context.encode(frame)
        if len(packets) != 1:
            raise AudioError(
                f"expected exactly one Opus packet per 60 ms frame, got {len(packets)}"
            )
        return bytes(packets[0])

    def close(self) -> None:
        """Release the codec context (idempotent; the GC also does this)."""
