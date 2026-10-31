"""Loopback control socket between the gateway and the MCP bridge (T016, plan C5).

Newline-delimited JSON over an ``AF_UNIX`` stream socket. The socket file is
the authentication boundary: mode 0660, inside a 0750 runtime directory, same
UID as the gateway. There is no TCP listener, so nothing new is exposed to the
LAN (plan D9, constitution I).

Framing, per line:

    request   {"id": 1, "method": "tools/list"|"tools/call", "params": {...}}
    response  {"id": 1, "result": ...} | {"id": 1, "error": {"code": .., "message": ..}}

A missing socket, a refused connection or a call with no live robot is always
the same fast answer: ``-32000 robot not connected``. The bridge must never
block a Hermes turn on a dead socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "ControlClient",
    "ControlError",
    "ControlServer",
    "NotConnected",
    "StaticBackend",
]

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603
SERVER_ERROR = -32000

CONTROL_SOCKET_MODE = 0o660
CONTROL_DIR_MODE = 0o750
DEFAULT_MAX_LINE_BYTES = 64 * 1024
DEFAULT_CLIENT_TIMEOUT_S = 0.2


class ControlError(Exception):
    def __init__(self, message: str, *, code: int = INTERNAL_ERROR) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class NotConnected(ControlError):
    def __init__(self, message: str = "robot not connected") -> None:
        super().__init__(message, code=SERVER_ERROR)


class ControlBackend(Protocol):
    async def list_tools(self) -> Any: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


class StaticBackend:
    """A backend for tests and for the ``--dry-run`` bridge path."""

    def __init__(
        self,
        tools: list[dict[str, Any]] | None = None,
        *,
        connected: bool = True,
        result: Any = None,
    ) -> None:
        self.tools = tools if tools is not None else [{"name": "robot_get_head_angles"}]
        self.connected = connected
        self.result = result if result is not None else {
            "content": [{"type": "text", "text": "ok"}],
            "isError": False,
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> Any:
        return {"tools": self.tools}

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if not self.connected:
            raise NotConnected()
        self.calls.append((name, arguments))
        return self.result


def _error(code: int, message: str) -> dict[str, Any]:
    return {"code": code, "message": message}


class ControlServer:
    """Serve ``tools/list`` and ``tools/call`` on a UNIX socket."""

    def __init__(
        self,
        path: str | Path,
        backend: ControlBackend,
        *,
        max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
    ) -> None:
        if max_line_bytes < 128:
            raise ValueError("max_line_bytes must be at least 128")
        self.path = Path(path)
        self.backend = backend
        self.max_line_bytes = max_line_bytes
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        if self._server is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        self._server = await asyncio.start_unix_server(
            self._handle, path=str(self.path), limit=self.max_line_bytes + 1
        )
        os.chmod(self.path, CONTROL_SOCKET_MODE)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    async def __aenter__(self) -> ControlServer:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                try:
                    line = await reader.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    await self._write(
                        writer, {"id": None, "error": _error(INVALID_REQUEST, "request too large")}
                    )
                    break
                if not line:
                    break
                await self._write(writer, await self._dispatch(line))
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _dispatch(self, line: bytes) -> dict[str, Any]:
        if len(line) > self.max_line_bytes:
            return {"id": None, "error": _error(INVALID_REQUEST, "request too large")}
        try:
            request = json.loads(line)
        except ValueError:
            return {"id": None, "error": _error(PARSE_ERROR, "parse error")}
        if not isinstance(request, dict):
            return {"id": None, "error": _error(INVALID_REQUEST, "request must be an object")}

        request_id = request.get("id") if isinstance(request.get("id"), int) else None
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return {"id": request_id, "error": _error(INVALID_REQUEST, "params must be an object")}

        try:
            if method == "tools/list":
                return {"id": request_id, "result": await self.backend.list_tools()}
            if method == "tools/call":
                name = params.get("name")
                if not isinstance(name, str) or not name:
                    return {
                        "id": request_id,
                        "error": _error(INVALID_REQUEST, "tools/call needs a name"),
                    }
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    return {
                        "id": request_id,
                        "error": _error(INVALID_REQUEST, "arguments must be an object"),
                    }
                result = await self.backend.call_tool(name, arguments)
                return {"id": request_id, "result": result}
        except NotConnected as exc:
            return {"id": request_id, "error": _error(exc.code, exc.message)}
        except ControlError as exc:
            return {"id": request_id, "error": _error(exc.code, exc.message)}
        except Exception as exc:  # noqa: BLE001 — the bridge must get an answer, not a traceback
            return {"id": request_id, "error": _error(INTERNAL_ERROR, f"internal error: {exc}")}

        return {
            "id": request_id,
            "error": _error(METHOD_NOT_FOUND, f"method not found: {method!r}"),
        }

    @staticmethod
    async def _write(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        writer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
        await writer.drain()


class ControlClient:
    """One request per connection; a dead socket is a fast ``NotConnected``."""

    def __init__(
        self,
        path: str | Path,
        *,
        timeout: float = DEFAULT_CLIENT_TIMEOUT_S,
        max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
    ) -> None:
        self.path = Path(path)
        self.timeout = timeout
        self.max_line_bytes = max_line_bytes
        self._next_id = 1

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        request_id = self._next_id
        self._next_id += 1
        payload: dict[str, Any] = {"id": request_id, "method": method}
        if params is not None:
            payload["params"] = params

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.path), limit=self.max_line_bytes + 1),
                timeout=self.timeout,
            )
        except (FileNotFoundError, ConnectionRefusedError, TimeoutError, OSError) as exc:
            raise NotConnected() from exc

        try:
            writer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
            await writer.drain()
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
            except (TimeoutError, ValueError, asyncio.LimitOverrunError) as exc:
                raise NotConnected() from exc
        except (ConnectionError, OSError) as exc:
            raise NotConnected() from exc
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

        if not line:
            raise NotConnected()
        try:
            response = json.loads(line)
        except ValueError as exc:
            raise ControlError("malformed response from the gateway") from exc
        if not isinstance(response, dict):
            raise ControlError("malformed response from the gateway")

        error = response.get("error")
        if isinstance(error, dict):
            code = error.get("code", INTERNAL_ERROR)
            message = str(error.get("message", "control error"))
            if code == SERVER_ERROR:
                raise NotConnected(message)
            raise ControlError(message, code=code)
        return response.get("result")


def make_tool_backend(
    list_tools: Callable[[], Awaitable[Any]],
    call_tool: Callable[[str, dict[str, Any]], Awaitable[Any]],
) -> ControlBackend:
    """Adapt two callables to the ``ControlBackend`` protocol."""

    class _Backend:
        async def list_tools(self) -> Any:
            return await list_tools()

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
            return await call_tool(name, arguments)

    return _Backend()
