"""Fake edge-tts: a monkeypatchable synthesiser with per-sentence failure.

edge-tts is a library call, not an HTTP service, so this fake is an object with
the same coroutine shape the real adapter exposes (``synthesize(text) -> mp3
bytes``). ``fail_on`` selects sentences that fail, which is how the
"one sentence failed, the rest continue" branch (FR-018) is reached.

The default audio is a real MP3 (silence), produced with PyAV on first use, so
tests exercise the actual decode -> resample -> frame path instead of asserting
against bytes the codec could never read (T022).
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Iterable

import av

__all__ = ["DEFAULT_MP3", "FakeEdgeTTS", "FakeTTSError", "make_mp3"]


class FakeTTSError(RuntimeError):
    """Raised for a sentence the fake is configured to fail."""


@functools.lru_cache(maxsize=16)
def make_mp3(seconds: float = 1.0, *, rate: int = 24_000) -> bytes:
    """Silence as a real MPEG-1 layer III stream lasting ``seconds``."""

    samples = max(1, int(rate * seconds))
    context = av.CodecContext.create("libmp3lame", "w")
    context.sample_rate = rate
    context.format = "s16"
    context.layout = "mono"
    context.bit_rate = 48_000
    context.open()
    frame = av.AudioFrame(format="s16", layout="mono", samples=samples)
    frame.sample_rate = rate
    frame.planes[0].update(bytes(samples * 2))
    packets = [*context.encode(frame), *context.encode(None)]
    return b"".join(bytes(packet) for packet in packets)


#: A little over a second of silence, in the same format edge-tts returns.
DEFAULT_MP3 = make_mp3(1.0)


class FakeEdgeTTS:
    def __init__(
        self,
        *,
        mp3_bytes: bytes | None = None,
        fail_on: Iterable[str] = (),
        delay_s: float = 0.0,
    ) -> None:
        self.mp3_bytes = DEFAULT_MP3 if mp3_bytes is None else mp3_bytes
        self.fail_on = set(fail_on)
        self.delay_s = delay_s
        self.calls: list[str] = []

    async def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if text in self.fail_on:
            raise FakeTTSError(f"fake edge-tts cannot synthesise {text!r}")
        return self.mp3_bytes
