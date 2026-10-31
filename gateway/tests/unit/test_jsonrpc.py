"""Unit tests for JSON-RPC 2.0 plumbing (T008)."""

from __future__ import annotations

import asyncio

import pytest

from jarvis_gateway import jsonrpc


def test_request_shape() -> None:
    assert jsonrpc.request(1, "tools/list", {"cursor": "a"}) == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {"cursor": "a"},
    }


def test_request_omits_params_when_none() -> None:
    assert jsonrpc.request(2, "initialize") == {"jsonrpc": "2.0", "id": 2, "method": "initialize"}


def test_response_and_error_shapes() -> None:
    assert jsonrpc.response(3, {"ok": True}) == {
        "jsonrpc": "2.0",
        "id": 3,
        "result": {"ok": True},
    }
    assert jsonrpc.error_response(3, -32601, "method not found") == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32601, "message": "method not found"},
    }


def test_error_constructors_use_the_documented_codes() -> None:
    assert jsonrpc.method_not_found("nope")["error"]["code"] == -32601
    assert jsonrpc.invalid_params("bad")["error"]["code"] == -32602
    assert jsonrpc.server_error("robot not connected")["error"]["code"] == -32000


def test_id_allocator_is_monotonic_per_session() -> None:
    first = jsonrpc.IdAllocator()
    second = jsonrpc.IdAllocator()

    assert [first.next(), first.next(), first.next()] == [1, 2, 3]
    assert second.next() == 1


async def test_resolve_matches_the_waiter_by_id() -> None:
    pending = jsonrpc.PendingRegistry()
    one = pending.register(1)
    two = pending.register(2)

    assert pending.resolve(2, {"jsonrpc": "2.0", "id": 2, "result": "b"}) is True

    assert two.done() and two.result()["result"] == "b"
    assert not one.done()


async def test_out_of_order_responses_land_on_the_right_futures() -> None:
    pending = jsonrpc.PendingRegistry()
    one = pending.register(1)
    two = pending.register(2)

    pending.resolve(2, {"id": 2, "result": "two"})
    pending.resolve(1, {"id": 1, "result": "one"})

    assert one.result()["result"] == "one"
    assert two.result()["result"] == "two"
    assert len(pending) == 0


async def test_wait_for_times_out_and_drops_the_late_response() -> None:
    pending = jsonrpc.PendingRegistry()
    pending.register(7)

    with pytest.raises(jsonrpc.RpcTimeout):
        await jsonrpc.wait_for(pending, 7, timeout=0.01)

    # The late answer is dropped, not raised into the caller and not left behind.
    assert pending.resolve(7, {"id": 7, "result": "late"}) is False
    assert len(pending) == 0


async def test_late_response_never_reaches_a_completed_caller() -> None:
    pending = jsonrpc.PendingRegistry()
    future = pending.register(9)
    pending.resolve(9, {"id": 9, "result": "quick"})

    assert await jsonrpc.wait_for(pending, 9, timeout=0.01) == {"id": 9, "result": "quick"}
    assert pending.resolve(9, {"id": 9, "result": "again"}) is False
    assert future.done()


async def test_duplicate_registration_is_refused() -> None:
    pending = jsonrpc.PendingRegistry()
    pending.register(4)

    with pytest.raises(jsonrpc.DuplicateIdError):
        pending.register(4)


async def test_wait_for_returns_a_failed_response_as_data() -> None:
    """A JSON-RPC error object is a response, not a transport failure."""

    pending = jsonrpc.PendingRegistry()
    pending.register(5)
    pending.resolve(5, jsonrpc.error_response(5, -32601, "method not found"))

    message = await jsonrpc.wait_for(pending, 5, timeout=0.01)

    assert message["error"]["code"] == -32601


async def test_fail_all_wakes_every_waiter() -> None:
    pending = jsonrpc.PendingRegistry()
    one = pending.register(1)
    two = pending.register(2)

    pending.fail_all(jsonrpc.RpcClosed("socket closed"))

    with pytest.raises(jsonrpc.RpcClosed):
        await one
    with pytest.raises(jsonrpc.RpcClosed):
        await asyncio.wait_for(two, timeout=0.01)
    assert len(pending) == 0
