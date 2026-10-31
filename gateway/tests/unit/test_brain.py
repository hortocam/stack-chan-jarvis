"""Streamed brain client against the Hermes API server (T021, plan C6).

The live contract (``docs/hermes-api-notes.md``, T011) fixes three things this
module must get right:

* the conversation is scoped by ``X-Hermes-Session-Key: stackchan-<device-id>``,
  so follow-up questions keep context;
* chunks carry ``delta.content`` **and sometimes ``delta.reasoning_content``** —
  only ``content`` may be spoken;
* the stream ends with ``data: [DONE]``; a stream that stops without it was cut.

Failures are typed so the turn can decide between an apology and silence.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis_gateway.brain import BrainCancelled, BrainClient, BrainError
from tests.fakes.fake_hermes import FakeHermes


def client_for(server: FakeHermes, *, timeout_s: float = 5.0) -> BrainClient:
    return BrainClient(
        f"{server.base_url}/v1",
        api_key="brain-key",
        model="hermes-agent",
        timeout_s=timeout_s,
    )


async def test_streams_deltas_and_returns_the_joined_reply(fake_hermes) -> None:
    fake_hermes.tokens = ["Hello", " there", ", sir."]
    deltas: list[str] = []

    async with client_for(fake_hermes) as client:
        reply = await client.stream(
            device_id="aa:bb:cc:dd:ee:ff", text="hello", on_delta=deltas.append
        )

    assert deltas == ["Hello", " there", ", sir."]
    assert reply.text == "Hello there, sir."
    assert reply.saw_done is True


async def test_sends_the_brain_contract_headers_and_a_streamed_body(fake_hermes) -> None:
    deltas: list[str] = []
    async with client_for(fake_hermes) as client:
        await client.stream(
            device_id="aa:bb:cc:dd:ee:ff", text="what time is it", on_delta=deltas.append
        )

    request = fake_hermes.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == "Bearer brain-key"
    assert request["session_key"] == "stackchan-aa:bb:cc:dd:ee:ff"
    assert request["body"]["model"] == "hermes-agent"
    assert request["body"]["stream"] is True
    assert request["body"]["messages"] == [{"role": "user", "content": "what time is it"}]


async def test_reasoning_deltas_are_never_sent_to_be_spoken(fake_hermes) -> None:
    """T011: ``delta.reasoning_content`` streams first; speaking it is the bug to catch."""

    fake_hermes.reasoning = "The user asked about the weather, so I should look it up."
    fake_hermes.tokens = ["It is", " mild today."]
    deltas: list[str] = []

    async with client_for(fake_hermes) as client:
        reply = await client.stream(device_id="dev", text="weather?", on_delta=deltas.append)

    assert deltas == ["It is", " mild today."]
    assert reply.text == "It is mild today."
    assert "weather" not in reply.text


async def test_a_chunk_split_across_network_writes_is_reassembled(fake_hermes) -> None:
    fake_hermes.split_chunks = True
    fake_hermes.pacing_s = 0.005
    fake_hermes.tokens = ["One", " two", " three"]
    deltas: list[str] = []

    async with client_for(fake_hermes) as client:
        reply = await client.stream(device_id="dev", text="count", on_delta=deltas.append)

    delta_text = "".join(deltas)
    assert delta_text == "One two three"
    assert reply.text == delta_text


async def test_an_error_before_the_first_token_is_a_status_error(fake_hermes) -> None:
    fake_hermes.error_before_first = True
    deltas: list[str] = []

    async with client_for(fake_hermes) as client:
        with pytest.raises(BrainError) as excinfo:
            await client.stream(device_id="dev", text="hi", on_delta=deltas.append)

    assert excinfo.value.kind == "status"
    assert deltas == []


async def test_a_stream_cut_after_the_first_token_keeps_the_partial_text(fake_hermes) -> None:
    fake_hermes.error_after_first = True
    fake_hermes.tokens = ["Half a sen", "tence that never arrives"]
    deltas: list[str] = []

    async with client_for(fake_hermes) as client:
        with pytest.raises(BrainError) as excinfo:
            await client.stream(device_id="dev", text="hi", on_delta=deltas.append)

    assert excinfo.value.kind == "interrupted"
    assert deltas == ["Half a sen"]
    assert excinfo.value.partial == "Half a sen"


async def test_a_stalled_stream_times_out_as_its_own_kind(fake_hermes) -> None:
    fake_hermes.stall_after_last_s = 1.0
    fake_hermes.tokens = ["Hello"]
    deltas: list[str] = []

    async with client_for(fake_hermes, timeout_s=0.2) as client:
        with pytest.raises(BrainError) as excinfo:
            await client.stream(device_id="dev", text="hi", on_delta=deltas.append)

    assert excinfo.value.kind == "timeout"
    assert excinfo.value.partial == "Hello"


async def test_cancellation_stops_the_stream_promptly_and_keeps_the_partial(fake_hermes) -> None:
    fake_hermes.pacing_s = 0.02
    fake_hermes.tokens = ["Alpha", " beta", " gamma", " delta"]
    cancel = asyncio.Event()
    deltas: list[str] = []

    def on_delta(token: str) -> None:
        deltas.append(token)
        if len(deltas) == 2:
            cancel.set()

    started = asyncio.get_running_loop().time()
    async with client_for(fake_hermes) as client:
        with pytest.raises(BrainCancelled) as excinfo:
            await client.stream(
                device_id="dev", text="hi", on_delta=on_delta, cancel=cancel
            )
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 1.0
    assert len(deltas) == 2
    assert excinfo.value.partial == "Alpha beta"
