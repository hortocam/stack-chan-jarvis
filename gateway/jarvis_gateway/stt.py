"""Transcription against the existing whisper service (T020, plan C6).

whisper on CT 912 is an OpenAI-compatible endpoint: a multipart WAV upload
returns ``{"text": "..."}``. The gateway never retries inside the turn — a
failed transcription is answered with the apology, not with a second attempt
that doubles the user's wait (FR-018).

The WAV is built in memory at 16 kHz mono PCM16, which is what the device's
Opus uplink decodes to; no file ever touches the disk.
"""

from __future__ import annotations

import io
import json
import logging
import wave

import aiohttp

__all__ = ["MAX_RESPONSE_BYTES", "SttClient", "SttError"]

logger = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 64 * 1024  # a transcript longer than this is not speech
MODEL = "whisper-1"


class SttError(RuntimeError):
    """The transcription service failed or answered something unusable."""

    def __init__(self, message: str, *, kind: str = "upstream") -> None:
        super().__init__(message)
        self.kind = kind


def wav_bytes(pcm16: bytes, sample_rate: int = 16_000) -> bytes:
    """Wrap mono PCM16 in a WAV container (whisper's documented upload shape)."""

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm16)
    return buffer.getvalue()


class SttClient:
    """One HTTP client for whisper; create it once per process, not per turn."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 10.0,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._session = session

    @property
    def url(self) -> str:
        return f"{self.base_url}/v1/audio/transcriptions"

    async def transcribe(self, pcm16: bytes) -> str:
        """Upload one utterance and return the stripped transcript (may be empty)."""

        if not pcm16:
            raise SttError("no audio to transcribe", kind="empty_input")

        form = aiohttp.FormData()
        form.add_field(
            "file",
            wav_bytes(pcm16),
            filename="utterance.wav",
            content_type="audio/wav",
        )
        form.add_field("model", MODEL)
        form.add_field("response_format", "json")

        timeout = aiohttp.ClientTimeout(total=self.timeout_s)
        session = self._get_session()
        try:
            async with session.post(self.url, data=form, timeout=timeout) as response:
                body = await response.read()
                if response.status >= 400:
                    raise SttError(
                        f"whisper returned {response.status}: {body[:200]!r}",
                        kind="status",
                    )
        except TimeoutError as exc:
            raise SttError(
                f"whisper timeout after {self.timeout_s:g}s", kind="timeout"
            ) from exc
        except aiohttp.ClientError as exc:
            raise SttError(f"whisper is unreachable: {exc}", kind="network") from exc

        return self._parse(body)

    @staticmethod
    def _parse(body: bytes) -> str:
        if len(body) > MAX_RESPONSE_BYTES:
            raise SttError(
                f"whisper response is larger than {MAX_RESPONSE_BYTES} bytes "
                f"({len(body)} received)",
                kind="oversized",
            )
        try:
            document = json.loads(body)
        except ValueError as exc:
            raise SttError(f"whisper response is not JSON: {body[:120]!r}", kind="format") from exc
        if not isinstance(document, dict) or not isinstance(document.get("text"), str):
            raise SttError(
                "whisper response has no string 'text' field", kind="format"
            )
        return document["text"].strip()

    def _get_session(self) -> aiohttp.ClientSession:
        """One HTTP session for the process, so the connection stays warm."""

        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> SttClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
