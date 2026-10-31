"""Fake Hermes brain: a streamed OpenAI-compatible chat endpoint.

Stands in for the API server on loopback. It streams ``data:`` chunks whose
shape matches the live server's SSE (recorded in T011), paces them, and can fail
either before the first token (a non-2xx status) or after it (the stream ends
without ``[DONE]``).

The gateway must send ``X-Hermes-Session-Key`` so that one robot keeps one
conversation; with ``require_session_key`` on, a request without it is rejected
with 400 and the omission is recorded in ``requests``.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from collections.abc import Sequence
from typing import Any

from aiohttp import web

__all__ = ["FakeHermes"]


class FakeHermes:
    def __init__(
        self,
        *,
        tokens: Sequence[str] = ("Hello", " there"),
        pacing_s: float = 0.0,
        error_before_first: bool = False,
        error_after_first: bool = False,
        require_session_key: bool = True,
        model: str = "hermes-agent",
        reasoning: str | None = None,
        split_chunks: bool = False,
        stall_after_last_s: float = 0.0,
        host: str = "127.0.0.1",
    ) -> None:
        self.tokens = list(tokens)
        self.pacing_s = pacing_s
        self.error_before_first = error_before_first
        self.error_after_first = error_after_first
        self.require_session_key = require_session_key
        self.model = model
        #: A ``reasoning_content`` delta written before any content delta (T011).
        self.reasoning = reasoning
        #: Write every SSE line in two pieces, split mid-line (partial-read test).
        self.split_chunks = split_chunks
        #: Keep the stream open (silent) this long after the last token, before [DONE].
        self.stall_after_last_s = stall_after_last_s
        self.host = host
        self.requests: list[dict[str, Any]] = []
        self._runner: web.AppRunner | None = None
        self._sock: socket.socket | None = None
        self._port = 0

    @property
    def port(self) -> int:
        return self._port

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self._port}"

    @property
    def url(self) -> str:
        return f"{self.base_url}/v1/chat/completions"

    async def start(self) -> str:
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self._handle_chat)
        app.router.add_get("/healthz", self._handle_health)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, 0))
        self._sock.listen(8)
        self._port = self._sock.getsockname()[1]
        site = web.SockSite(self._runner, self._sock)
        await site.start()
        return self.url

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        self._port = 0

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    def _chunk(self, token: str, *, field: str = "content") -> bytes:
        payload = {
            "id": "chatcmpl-fake",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": self.model,
            "choices": [{"index": 0, "delta": {field: token}, "finish_reason": None}],
        }
        return f"data: {json.dumps(payload)}\n\n".encode()

    async def _write(self, response: web.StreamResponse, payload: bytes) -> None:
        """Write one SSE event, optionally split across two writes."""

        if self.split_chunks and len(payload) > 2:
            middle = len(payload) // 2
            await response.write(payload[:middle])
            await asyncio.sleep(0.001)
            await response.write(payload[middle:])
        else:
            await response.write(payload)

    async def _handle_chat(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        record: dict[str, Any] = {
            "path": request.path,
            "authorization": request.headers.get("Authorization"),
            "session_key": request.headers.get("X-Hermes-Session-Key"),
            "body": body,
            "streamed_tokens": 0,
        }
        self.requests.append(record)

        if self.require_session_key and not record["session_key"]:
            return web.json_response(
                {"error": {"message": "missing X-Hermes-Session-Key"}}, status=400
            )

        if self.error_before_first:
            return web.json_response(
                {"error": {"message": "fake hermes rejected the turn"}}, status=502
            )

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)

        if self.reasoning:
            await self._write(response, self._chunk(self.reasoning, field="reasoning_content"))
            await response.write(b": reasoning done\n\n")

        for index, token in enumerate(self.tokens):
            if index and self.pacing_s:
                await asyncio.sleep(self.pacing_s)
            await self._write(response, self._chunk(token))
            record["streamed_tokens"] += 1
            if self.error_after_first and index == 0:
                # The upstream dies mid-stream: no [DONE], connection closed.
                await response.write_eof()
                return response

        if self.stall_after_last_s:
            await asyncio.sleep(self.stall_after_last_s)

        await self._write(response, b"data: [DONE]\n\n")
        await response.write_eof()
        return response
