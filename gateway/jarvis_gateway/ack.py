"""Cached acknowledgement phrase, synthesized once and replayed per turn (P3.1).

The brain takes seconds to produce its first token; a short "Right." makes the
robot feel alive while it thinks (SC-001). The phrase is synthesised on the
first boot with the configured voice, decoded and Opus-framed exactly like a
reply sentence, then kept in memory and on disk so later turns pay no synthesis
and no decode.

The cache file is a sequence of ``uint16`` big-endian packet lengths followed by
the packet bytes; its name is a digest of the cache version, voice and text, so
changing either the voice or the phrase misses the cache instead of replaying
the wrong words. A corrupt or unreadable cache is dropped and re-synthesised.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import struct
from pathlib import Path

from .tts import TtsError

__all__ = ["AckVoice"]

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
_LENGTH = struct.Struct(">H")
_MAX_PACKET_BYTES = 8000


class AckVoice:
    """One short phrase, synthesised once per (voice, text) and replayed."""

    def __init__(self, text: str, *, voice: str, cache_dir: str | Path) -> None:
        self.text = (text or "").strip()
        self.voice = voice
        self.cache_dir = Path(cache_dir)
        self._frames: list[bytes] | None = None
        self._unavailable = False
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.text) and not self._unavailable

    async def frames(self, tts) -> list[bytes] | None:
        """The phrase's Opus frames, or ``None`` when it cannot be spoken."""

        if not self.enabled:
            return None
        async with self._lock:
            if self._frames is not None:
                return list(self._frames)
            path = self._cache_path()
            cached = self._load(path)
            if cached is not None:
                self._frames = cached
                return list(cached)

            try:
                frames = [frame async for frame in tts.frames(self.text)]
            except TtsError as exc:
                self._unavailable = True
                logger.warning(
                    "acknowledgement phrase could not be synthesised; continuing without it",
                    extra={"kind": exc.kind},
                )
                return None
            if not frames:
                self._unavailable = True
                return None
            self._frames = frames
            self._store(path, frames)
            return list(frames)

    # -- cache -------------------------------------------------------------

    def _cache_path(self) -> Path:
        digest = hashlib.sha256(
            f"{CACHE_VERSION}\0{self.voice}\0{self.text}".encode()
        ).hexdigest()[:16]
        return self.cache_dir / f"ack-{digest}.opus"

    def _load(self, path: Path) -> list[bytes] | None:
        try:
            data = path.read_bytes()
        except OSError:
            return None
        frames: list[bytes] = []
        offset = 0
        try:
            while offset < len(data):
                (length,) = _LENGTH.unpack_from(data, offset)
                offset += _LENGTH.size
                if length == 0 or length > _MAX_PACKET_BYTES or offset + length > len(data):
                    raise ValueError("bad packet length")
                frames.append(data[offset : offset + length])
                offset += length
        except (struct.error, ValueError):
            logger.warning("ignoring a corrupt acknowledgement cache at %s", path)
            return None
        return frames or None

    def _store(self, path: Path, frames: list[bytes]) -> None:
        payload = bytearray()
        for frame in frames:
            payload += _LENGTH.pack(len(frame))
            payload += frame
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(payload)
            os.replace(temporary, path)
        except OSError as exc:
            logger.warning("could not cache the acknowledgement phrase: %s", exc)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
