"""Streamed chat completion against the Hermes API server (T021, plan C6).

What the live server actually does is recorded in ``docs/hermes-api-notes.md``
(T011) and this module encodes exactly that:

* ``POST {base}/chat/completions`` with ``Authorization: Bearer <key>``,
  ``X-Hermes-Session-Key: stackchan-<device-id>`` and ``stream: true``;
* SSE ``data:`` lines of ``chat.completion.chunk`` objects, ending ``data: [DONE]``;
* the only field that may be spoken is ``choices[0].delta.content``. Some replies
  stream ``delta.reasoning_content`` first; concatenating "the first string field"
  would speak the model's reasoning down the robot's throat.

The client is cancellable: the caller's ``asyncio.Event`` is polled while waiting
for bytes, so a cancelled turn stops within ~100 ms even when the brain is silent.
Every failure carries the partial text, because a half-spoken answer is different
from an unanswered one (FR-018).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

import aiohttp

__all__ = ["BrainCancelled", "BrainClient", "BrainError", "BrainReply", "session_key"]

logger = logging.getLogger(__name__)

#: Longest single wait for bytes before the cancellation flag is re-read.
_POLL_S = 0.1


def session_key(device_id: str) -> str:
    """The per-device conversation scope (plan C6/D5)."""

    if not device_id:
        raise ValueError("device_id is required")
    return f"stackchan-{device_id}"


class BrainError(RuntimeError):
    """A brain call that did not produce a complete reply."""

    def __init__(self, message: str, *, kind: str, partial: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.partial = partial


class BrainCancelled(BrainError):
    """The turn was cancelled (abort/barge-in) while the brain was streaming."""

    def __init__(self, message: str = "brain stream cancelled", *, partial: str = "") -> None:
        super().__init__(message, kind="cancelled", partial=partial)


@dataclass(frozen=True, slots=True)
class BrainReply:
    text: str
    saw_done: bool


class BrainClient:
    """One streaming client for the loopback brain; reusable across turns."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str,
        model: str,
        timeout_s: float = 30.0,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        if not api_key:
            raise ValueError("api_key is required")
        if not model:
            raise ValueError("model is required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self._session = session

    @property
    def url(self) -> str:
        return f"{self.base_url}/chat/completions"

    async def stream(
        self,
        *,
        device_id: str,
        text: str,
        on_delta: Callable[[str], None],
        cancel: asyncio.Event | None = None,
    ) -> BrainReply:
        """Stream one reply, calling ``on_delta`` for each content fragment."""

        body = {
            "model": self.model,
            "stream": True,
            "messages": [{"role": "user", "content": text}],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Hermes-Session-Key": session_key(device_id),
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        timeout = aiohttp.ClientTimeout(total=None)  # the turn owns the deadline
        session = self._get_session()
        parts: list[str] = []
        try:
            async with session.post(
                self.url, json=body, headers=headers, timeout=timeout
            ) as response:
                if response.status != 200:
                    detail = (await response.read())[:200]
                    raise BrainError(
                        f"brain returned {response.status}: {detail!r}", kind="status"
                    )
                saw_done = await self._consume(
                    response, parts=parts, on_delta=on_delta, cancel=cancel
                )
        except TimeoutError as exc:  # pragma: no cover - defensive, _consume owns deadlines
            raise BrainError("brain timeout", kind="timeout", partial="".join(parts)) from exc
        except aiohttp.ClientError as exc:
            raise BrainError(
                f"brain stream failed: {exc}", kind="interrupted", partial="".join(parts)
            ) from exc

        return BrainReply(text="".join(parts), saw_done=saw_done)

    async def _consume(
        self,
        response: aiohttp.ClientResponse,
        *,
        parts: list[str],
        on_delta: Callable[[str], None],
        cancel: asyncio.Event | None,
    ) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_s
        buffer = b""
        saw_done = False

        while not saw_done:
            if cancel is not None and cancel.is_set():
                raise BrainCancelled(partial="".join(parts))
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise BrainError(
                    f"brain produced no complete stream within {self.timeout_s:g}s",
                    kind="timeout",
                    partial="".join(parts),
                )
            try:
                chunk = await asyncio.wait_for(
                    response.content.readany(), timeout=min(_POLL_S, remaining)
                )
            except TimeoutError:
                continue
            except aiohttp.ClientError as exc:
                raise BrainError(
                    f"brain stream failed: {exc}", kind="interrupted", partial="".join(parts)
                ) from exc
            if not chunk:
                raise BrainError(
                    "brain stream ended without [DONE]",
                    kind="interrupted",
                    partial="".join(parts),
                )
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                saw_done = self._handle_line(line.strip(), parts, on_delta)
                if saw_done:
                    break
        return saw_done

    @staticmethod
    def _handle_line(
        line: bytes, parts: list[str], on_delta: Callable[[str], None]
    ) -> bool:
        """Feed one SSE line; returns True on ``[DONE]``."""

        if not line or line.startswith(b":") or not line.startswith(b"data:"):
            return False
        data = line[len(b"data:") :].strip()
        if data == b"[DONE]":
            return True
        try:
            document = json.loads(data)
        except ValueError:
            logger.warning("ignoring unparseable SSE data line: %r", data[:120])
            return False
        choices = document.get("choices")
        if not isinstance(choices, list) or not choices:
            return False
        delta = choices[0].get("delta")
        if not isinstance(delta, dict):
            return False
        # ONLY content. reasoning_content is on the wire (T011) and is not speech.
        content = delta.get("content")
        if isinstance(content, str) and content:
            parts.append(content)
            on_delta(content)
        return False

    def _get_session(self) -> aiohttp.ClientSession:
        """One HTTP session for the process, so the connection stays warm."""

        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> BrainClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
