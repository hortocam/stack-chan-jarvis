"""Speech synthesis: edge-tts MP3 to 24 kHz Opus frames (T022, plan C6).

edge-tts is a library call with no account and no vendor integration
(constitution III). It returns MP3; the robot wants one Opus packet per 60 ms at
24 kHz mono. The conversion is: decode MP3 with the pinned PyAV codec layer,
resample to 24 kHz, pad only the final 60 ms frame, encode each frame.

``TtsEngine.frames`` is an async generator, so the turn can interleave
synthesis with sending and pull the cancel hook between frames (FR-010's
plumbing lands in T037; the hook exists here).
"""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import AsyncIterator

import av

from .audio import DOWNLINK_RATE, OpusEncoder, frame_60ms

__all__ = ["EdgeTts", "TtsEngine", "TtsError", "mp3_to_pcm24", "pcm24_to_frames"]

logger = logging.getLogger(__name__)


class TtsError(RuntimeError):
    """Synthesis, decoding or framing failed for one sentence."""

    def __init__(self, message: str, *, kind: str = "upstream") -> None:
        super().__init__(message)
        self.kind = kind


def mp3_to_pcm24(data: bytes) -> bytes:
    """Decode an MP3 stream to mono PCM16 at 24 kHz."""

    if not data:
        raise TtsError("synthesis returned no audio", kind="empty")
    try:
        container = av.open(io.BytesIO(data), mode="r", format="mp3")
    except (av.FFmpegError, ValueError) as exc:
        raise TtsError(f"synthesis returned undecodable MP3: {exc}", kind="decode") from exc
    try:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=DOWNLINK_RATE)
        out = bytearray()

        def drain(frames) -> None:
            for frame in frames:
                for chunk in resampler.resample(frame):
                    out.extend(
                        bytes(memoryview(chunk.planes[0])[: chunk.samples * 2])
                    )

        drain(container.decode(stream))
        drain(resampler.resample(None))
    except (av.FFmpegError, IndexError, ValueError) as exc:
        raise TtsError(f"synthesis returned undecodable MP3: {exc}", kind="decode") from exc
    finally:
        container.close()

    if not out:
        raise TtsError("synthesis decoded to no audio", kind="decode")
    return bytes(out)


def pcm24_to_frames(pcm16: bytes) -> list[bytes]:
    """Split 24 kHz mono PCM16 into 60 ms frames (tail padded, nothing dropped)."""

    return frame_60ms(pcm16, DOWNLINK_RATE)


class EdgeTts:
    """The real synthesiser: ``edge_tts.Communicate`` with the configured voice."""

    def __init__(self, voice: str, *, timeout_s: float = 15.0) -> None:
        if not voice:
            raise ValueError("voice is required")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.voice = voice
        self.timeout_s = timeout_s

    async def synthesize(self, text: str) -> bytes:
        text = text.strip()
        if not text:
            raise TtsError("cannot synthesise empty text", kind="empty")
        try:
            import edge_tts
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            raise TtsError(f"edge-tts is not installed: {exc}", kind="import") from exc

        communicate = edge_tts.Communicate(text, self.voice)
        audio = bytearray()
        try:
            async with asyncio.timeout(self.timeout_s):
                async for chunk in communicate.stream():
                    if chunk.get("type") == "audio":
                        audio += chunk["data"]
        except TimeoutError as exc:
            raise TtsError(
                f"edge-tts timeout after {self.timeout_s:g}s", kind="timeout"
            ) from exc
        except Exception as exc:  # edge-tts raises its own exception types
            raise TtsError(f"edge-tts failed: {exc}", kind="upstream") from exc
        if not audio:
            raise TtsError("edge-tts returned no audio", kind="empty")
        return bytes(audio)


class TtsEngine:
    """Turns one sentence into Opus frames, with a cancel hook between frames."""

    def __init__(self, synthesizer) -> None:
        self._synthesizer = synthesizer

    async def frames(
        self, text: str, *, cancel: asyncio.Event | None = None
    ) -> AsyncIterator[bytes]:
        try:
            mp3 = await self._synthesizer.synthesize(text)
        except TtsError:
            raise
        except Exception as exc:
            raise TtsError(f"synthesis failed for {text!r}: {exc}", kind="upstream") from exc

        encoder = OpusEncoder(DOWNLINK_RATE)
        try:
            for frame in pcm24_to_frames(mp3_to_pcm24(mp3)):
                if cancel is not None and cancel.is_set():
                    return
                yield encoder.encode(frame)
        finally:
            encoder.close()
