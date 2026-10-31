"""Energy VAD with hysteresis and hard bounds (T012, plan D3).

The device has acoustic echo cancellation and a quiet room, so RMS energy over
60 ms frames is an adequate and — unlike a model — fully deterministic end-of-
speech detector. Two thresholds give hysteresis: a frame opens an utterance only
above ``start_rms``, but keeps it open above the lower ``continue_rms``, so a
quiet syllable inside a sentence does not count as silence.

The bounds are the ones the spec fixes (FR-005): the utterance ends after
``silence_ms`` of silence, is discarded as ``too_short`` below
``min_speech_ms`` of voiced audio, and is force-closed at ``max_utterance_s``
with ``forced=True`` so memory cannot grow with a stuck microphone.

The VAD owns no clock and no I/O: the caller pushes one 60 ms frame at a time
and gets a completed ``Utterance`` back when one ends. Frame indices are
0-based and count every frame pushed, so tests can point at the fixture they
fed.
"""

from __future__ import annotations

import math
from array import array
from dataclasses import dataclass, fields

__all__ = ["Utterance", "Vad", "VadConfig", "frame_rms"]

FRAME_MS = 60
FRAME_SAMPLES = 960
FRAME_BYTES = FRAME_SAMPLES * 2


def frame_rms(frame: bytes | bytearray) -> float:
    """Root-mean-square amplitude of one mono PCM16 frame."""

    if len(frame) % 2:
        raise ValueError("PCM16 frames must have an even number of bytes")
    if not frame:
        return 0.0
    samples = array("h")
    samples.frombytes(bytes(frame))
    total = 0
    for sample in samples:
        total += sample * sample
    return math.sqrt(total / len(samples))


@dataclass(frozen=True, slots=True)
class VadConfig:
    silence_ms: int = 300
    min_speech_ms: int = 250
    max_utterance_s: float = 30.0
    frame_ms: int = FRAME_MS
    start_rms: float = 1000.0
    continue_rms: float = 500.0
    start_frames: int = 2

    def __post_init__(self) -> None:
        if self.frame_ms <= 0:
            raise ValueError("frame_ms must be positive")
        if self.silence_ms <= 0:
            raise ValueError("silence_ms must be positive")
        if self.min_speech_ms < 0:
            raise ValueError("min_speech_ms must not be negative")
        if self.max_utterance_s <= 0:
            raise ValueError("max_utterance_s must be positive")
        if self.start_frames < 1:
            raise ValueError("start_frames must be at least one")
        if not 0 < self.continue_rms < self.start_rms:
            raise ValueError("thresholds must satisfy 0 < continue_rms < start_rms")


@dataclass(slots=True)
class Utterance:
    """One captured question, bounded and labelled with why it ended."""

    pcm: bytes
    start_frame: int
    end_frame: int
    voiced_ms: int
    reason: str
    forced: bool = False
    too_short: bool = False
    started_at: float | None = None
    ended_at: float | None = None


class Vad:
    """Frame-at-a-time end-of-speech detector."""

    def __init__(
        self,
        config: VadConfig | None = None,
        *,
        silence_ms: int | None = None,
        min_speech_ms: int | None = None,
        max_utterance_s: float | None = None,
        start_rms: float | None = None,
        continue_rms: float | None = None,
    ) -> None:
        base = config or VadConfig()
        overrides = {
            "silence_ms": silence_ms,
            "min_speech_ms": min_speech_ms,
            "max_utterance_s": max_utterance_s,
            "start_rms": start_rms,
            "continue_rms": continue_rms,
        }
        values = {
            item.name: getattr(base, item.name)
            for item in fields(VadConfig)
            if item.name in overrides
        }
        values.update({name: value for name, value in overrides.items() if value is not None})
        self.config = VadConfig(**values)

        self._frame_index = 0
        self._speaking = False
        self._start_frame = 0
        self._frames: list[bytes] = []
        self._candidate: list[bytes] = []
        self._voiced_frames = 0
        self._silence_frames = 0

    @property
    def speaking(self) -> bool:
        return self._speaking

    @property
    def current_frames(self) -> int:
        return len(self._frames)

    def push(self, frame: bytes) -> Utterance | None:
        """Add one 60 ms frame; return the utterance it completed, if any."""

        if not isinstance(frame, (bytes, bytearray)):
            raise TypeError("frames must be bytes")
        if len(frame) % 2:
            raise ValueError("PCM16 frames must have an even number of bytes")
        frame = bytes(frame)
        index = self._frame_index
        self._frame_index += 1
        energy = frame_rms(frame)

        if not self._speaking:
            if energy < self.config.start_rms:
                self._candidate.clear()
                return None
            self._candidate.append(frame)
            if len(self._candidate) < self.config.start_frames:
                return None
            self._speaking = True
            self._start_frame = index - len(self._candidate) + 1
            self._frames = list(self._candidate)
            self._candidate.clear()
            self._voiced_frames = len(self._frames)
            self._silence_frames = 0
            return self._check_cap(index)

        self._frames.append(frame)
        if energy >= self.config.continue_rms:
            self._voiced_frames += 1
            self._silence_frames = 0
        else:
            self._silence_frames += 1

        capped = self._check_cap(index)
        if capped is not None:
            return capped

        if self._silence_frames * self.config.frame_ms >= self.config.silence_ms:
            end_frame = index + 1 - self._silence_frames
            return self._complete(end_frame, "silence_for_detected")
        return None

    def finish(self, reason: str = "stop") -> Utterance | None:
        """Close the utterance at the end of the stream or on a device stop."""

        if not self._speaking:
            self._candidate.clear()
            return None
        return self._complete(self._frame_index, reason)

    def reset(self) -> None:
        self._speaking = False
        self._frames.clear()
        self._candidate.clear()
        self._voiced_frames = 0
        self._silence_frames = 0

    def _check_cap(self, index: int) -> Utterance | None:
        elapsed = (index - self._start_frame + 1) * self.config.frame_ms / 1000.0
        if elapsed >= self.config.max_utterance_s:
            return self._complete(index + 1, "max_duration", forced=True)
        return None

    def _complete(self, end_frame: int, reason: str, *, forced: bool = False) -> Utterance:
        frames = self._frames[: max(0, end_frame - self._start_frame)]
        voiced_ms = self._voiced_frames * self.config.frame_ms
        too_short = voiced_ms < self.config.min_speech_ms
        utterance = Utterance(
            pcm=b"" if too_short else b"".join(frames),
            start_frame=self._start_frame,
            end_frame=end_frame,
            voiced_ms=voiced_ms,
            reason=reason,
            forced=forced,
            too_short=too_short,
        )
        self.reset()
        return utterance
