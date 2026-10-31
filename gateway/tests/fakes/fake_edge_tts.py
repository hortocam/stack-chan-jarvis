"""Fake edge-tts: a monkeypatchable synthesiser with per-sentence failure.

edge-tts is a library call, not an HTTP service, so this fake is an object with
the same coroutine shape the real adapter will expose (``synthesize(text) ->
mp3 bytes``). ``fail_on`` selects sentences that fail, which is how the
"one sentence failed, the rest continue" branch (FR-018) is reached.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

__all__ = ["FakeEdgeTTS", "FakeTTSError"]

# A minimal MPEG audio frame header; enough for a fake that never decodes.
DEFAULT_MP3 = b"\xff\xfb\x90\x00" + b"\x00" * 100


class FakeTTSError(RuntimeError):
    """Raised for a sentence the fake is configured to fail."""


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
