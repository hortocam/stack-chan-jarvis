"""JSON-RPC 2.0 plumbing shared by the MCP client and the bridge (T008).

The gateway is a JSON-RPC *client* of the robot over the voice socket (plan
C3) and a JSON-RPC *client* of its own control socket; the bridge is the
JSON-RPC *server* Hermes talks to (plan C4). All three need the same small
pieces: message constructors, per-session ids that only ever increase, and a
registry that matches a response back to the request that is waiting for it.

A response that arrives after its deadline is dropped silently: the caller has
already moved on, and raising into a dead future would surface as an
"exception was never retrieved" warning in the gateway's log.
"""

from __future__ import annotations

import asyncio
from typing import Any

__all__ = [
    "DuplicateIdError",
    "IdAllocator",
    "PendingRegistry",
    "RpcClosed",
    "RpcError",
    "RpcTimeout",
    "error",
    "error_response",
    "id_allocator",
    "invalid_params",
    "method_not_found",
    "notification",
    "request",
    "response",
    "server_error",
    "wait_for",
]

JSONRPC_VERSION = "2.0"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
SERVER_ERROR = -32000

_MISSING = object()


class RpcError(RuntimeError):
    """Transport-level failure of a JSON-RPC exchange."""


class RpcTimeout(RpcError):
    """No response arrived before the deadline; the request was dropped."""


class RpcClosed(RpcError):
    """The socket went away with requests still pending."""


class DuplicateIdError(RpcError):
    """A request id was registered twice on one session."""


def request(rpc_id: int, method: str, params: Any = _MISSING) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": rpc_id, "method": method}
    if params is not _MISSING:
        message["params"] = params
    return message


def notification(method: str, params: Any = _MISSING) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not _MISSING:
        message["params"] = params
    return message


def response(rpc_id: int, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": rpc_id, "result": result}


def error(code: int, message: str, data: Any = _MISSING) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if data is not _MISSING:
        body["data"] = data
    return body


def error_response(
    rpc_id: int | None, code: int, message: str, data: Any = _MISSING
) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": rpc_id, "error": error(code, message, data)}


def method_not_found(message: str = "method not found") -> dict[str, Any]:
    return error_response(None, METHOD_NOT_FOUND, message)


def invalid_params(message: str) -> dict[str, Any]:
    return error_response(None, INVALID_PARAMS, message)


def server_error(message: str, data: Any = _MISSING) -> dict[str, Any]:
    return error_response(None, SERVER_ERROR, message, data)


class IdAllocator:
    """Monotonically increasing request ids, one allocator per session."""

    def __init__(self, start: int = 1) -> None:
        self._next = start

    def next(self) -> int:
        value = self._next
        self._next += 1
        return value


def id_allocator(start: int = 1) -> IdAllocator:
    return IdAllocator(start)


class PendingRegistry:
    """Request id → future, for one socket."""

    def __init__(self) -> None:
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}

    def register(self, rpc_id: int) -> asyncio.Future[dict[str, Any]]:
        if rpc_id in self._pending:
            raise DuplicateIdError(f"request id {rpc_id} is already in flight")
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[rpc_id] = future
        return future

    def get(self, rpc_id: int) -> asyncio.Future[dict[str, Any]]:
        try:
            return self._pending[rpc_id]
        except KeyError as exc:
            raise LookupError(f"no pending request with id {rpc_id}") from exc

    def resolve(self, rpc_id: int, message: dict[str, Any]) -> bool:
        """Deliver a response. Unknown (late) ids are dropped, returning False."""

        future = self._pending.get(rpc_id)
        if future is None or future.done():
            return False
        future.set_result(message)
        return True

    def fail(self, rpc_id: int, exc: BaseException) -> bool:
        future = self._pending.pop(rpc_id, None)
        if future is None or future.done():
            return False
        future.set_exception(exc)
        return True

    def discard(self, rpc_id: int) -> bool:
        future = self._pending.pop(rpc_id, None)
        if future is None:
            return False
        if not future.done():
            future.cancel()
        return True

    def fail_all(self, exc: BaseException) -> None:
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(exc)

    @property
    def pending_ids(self) -> tuple[int, ...]:
        return tuple(rpc_id for rpc_id, future in self._pending.items() if not future.done())

    def __len__(self) -> int:
        return sum(1 for future in self._pending.values() if not future.done())


async def wait_for(
    pending: PendingRegistry, rpc_id: int, timeout: float  # noqa: ASYNC109 — a per-request deadline, not a task scope
) -> dict[str, Any]:
    """Await one response with a deadline, then drop the id.

    One future per id is the single-flight rule: a second response for the same
    id can never reach this caller, because the entry is gone once it arrives
    or times out.
    """

    future = pending.get(rpc_id)
    try:
        message = await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
    except TimeoutError as exc:
        pending.discard(rpc_id)
        raise RpcTimeout(f"request {rpc_id} timed out after {timeout:.3f}s") from exc
    pending.discard(rpc_id)
    return message
