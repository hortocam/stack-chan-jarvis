"""Fake whisper: an OpenAI-compatible transcription endpoint on an ephemeral port.

Modes (`mode=`): ``ok`` returns ``text``, ``empty`` returns a blank transcript,
``error`` returns ``status`` with a JSON error body. ``delay_s`` postpones the
answer, which is how the gateway's timeout path is exercised. Every request is
recorded in ``requests`` so a test can assert what was actually uploaded.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

from aiohttp import web

__all__ = ["FakeWhisper"]

_MODES = frozenset({"ok", "empty", "error"})


class FakeWhisper:
    def __init__(
        self,
        *,
        text: str = "what time is it",
        status: int = 500,
        delay_s: float = 0.0,
        mode: str = "ok",
        host: str = "127.0.0.1",
    ) -> None:
        if mode not in _MODES:
            raise ValueError(f"mode must be one of {sorted(_MODES)}, got {mode!r}")
        self.text = text
        self.status = status
        self.delay_s = delay_s
        self.mode = mode
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
        return f"{self.base_url}/v1/audio/transcriptions"

    async def start(self) -> str:
        app = web.Application()
        app.router.add_post("/v1/audio/transcriptions", self._handle_transcription)
        app.router.add_get("/health", self._handle_health)

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

    async def _handle_transcription(self, request: web.Request) -> web.Response:
        record: dict[str, Any] = {
            "path": request.path,
            "content_type": request.content_type,
            "file_field": None,
            "filename": None,
            "model": None,
            "body": b"",
        }
        self.requests.append(record)

        if request.content_type.startswith("multipart/"):
            reader = await request.multipart()
            while (part := await reader.next()) is not None:
                if part.name == "file":
                    record["file_field"] = "file"
                    record["filename"] = part.filename
                    record["body"] = await part.read()
                else:
                    record[part.name] = await part.text()

        if self.delay_s:
            await asyncio.sleep(self.delay_s)

        if self.mode == "error":
            return web.json_response(
                {"error": {"message": "fake whisper refused the upload"}}, status=self.status
            )
        if self.mode == "empty":
            return web.json_response({"text": ""})
        return web.json_response({"text": self.text})
