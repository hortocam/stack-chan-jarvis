"""Unit tests for the fake Hermes brain (T006).

The fake stands in for the OpenAI-compatible API server on loopback. Its modes
are the contract for T021: token pacing, an error before the first token, an
error after the first token, and the `X-Hermes-Session-Key` header the gateway
must send so that per-device conversation context is preserved.
"""

from __future__ import annotations

import json
import time

import aiohttp

from tests.fakes.fake_hermes import FakeHermes

BODY = {
    "model": "hermes-agent",
    "stream": True,
    "messages": [{"role": "user", "content": "what time is it"}],
}


async def _stream(
    hermes: FakeHermes, headers: dict[str, str] | None = None
) -> tuple[int, list[str]]:
    async with aiohttp.ClientSession() as session:
        async with session.post(hermes.url, json=BODY, headers=headers) as response:
            status = response.status
            if status != 200:
                await response.read()
                return status, []
            lines: list[str] = []
            async for raw in response.content:
                lines.append(raw.decode("utf-8"))
            return status, lines


def _events(chunks: list[str]) -> list[str]:
    text = "".join(chunks)
    return [line[len("data: ") :] for line in text.splitlines() if line.startswith("data: ")]


def _text(chunks: list[str]) -> str:
    out = []
    for event in _events(chunks):
        if event == "[DONE]":
            continue
        delta = json.loads(event)["choices"][0]["delta"]
        out.append(delta.get("content", ""))
    return "".join(out)


async def test_streams_tokens_in_order_and_terminates_with_done() -> None:
    hermes = FakeHermes(tokens=["Hello", " there", " friend"])
    await hermes.start()
    try:
        status, chunks = await _stream(hermes, {"X-Hermes-Session-Key": "stackchan-aa:bb:cc"})
    finally:
        await hermes.stop()

    assert status == 200
    assert _text(chunks) == "Hello there friend"
    assert _events(chunks)[-1] == "[DONE]"
    assert hermes.requests[-1]["session_key"] == "stackchan-aa:bb:cc"
    assert hermes.requests[-1]["body"]["stream"] is True


async def test_missing_session_key_is_recorded_and_rejected() -> None:
    hermes = FakeHermes(tokens=["Hello"])
    await hermes.start()
    try:
        status, _ = await _stream(hermes)
    finally:
        await hermes.stop()

    assert status == 400
    assert hermes.requests[-1]["session_key"] is None


async def test_pacing_delays_between_chunks() -> None:
    hermes = FakeHermes(tokens=["one", "two", "three"], pacing_s=0.15)
    await hermes.start()
    try:
        started = time.monotonic()
        status, chunks = await _stream(hermes, {"X-Hermes-Session-Key": "stackchan-aa:bb:cc"})
        elapsed = time.monotonic() - started
    finally:
        await hermes.stop()

    assert status == 200
    assert _text(chunks) == "onetwothree"
    assert elapsed >= 0.28


async def test_error_before_first_token_returns_a_status_not_a_stream() -> None:
    hermes = FakeHermes(error_before_first=True)
    await hermes.start()
    try:
        status, chunks = await _stream(hermes, {"X-Hermes-Session-Key": "stackchan-aa:bb:cc"})
    finally:
        await hermes.stop()

    assert status == 502
    assert chunks == []
    assert hermes.requests[-1]["streamed_tokens"] == 0


async def test_error_after_first_token_truncates_without_done() -> None:
    hermes = FakeHermes(tokens=["Hello", " there"], error_after_first=True)
    await hermes.start()
    try:
        status, chunks = await _stream(hermes, {"X-Hermes-Session-Key": "stackchan-aa:bb:cc"})
    finally:
        await hermes.stop()

    assert status == 200
    assert _text(chunks) == "Hello"
    assert "[DONE]" not in _events(chunks)
    assert hermes.requests[-1]["streamed_tokens"] == 1


async def test_sse_chunk_shape_matches_the_openai_stream() -> None:
    hermes = FakeHermes(tokens=["Hi"])
    await hermes.start()
    try:
        _, chunks = await _stream(hermes, {"X-Hermes-Session-Key": "stackchan-aa:bb:cc"})
    finally:
        await hermes.stop()

    first = json.loads(_events(chunks)[0])
    assert first["object"] == "chat.completion.chunk"
    assert first["choices"][0]["index"] == 0
    assert first["choices"][0]["delta"] == {"content": "Hi"}


async def test_fixture_starts_on_an_ephemeral_port() -> None:
    hermes = FakeHermes()
    assert hermes.url.endswith("/v1/chat/completions")
    await hermes.start()
    try:
        assert hermes.port > 0
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{hermes.base_url}/healthz") as response:
                assert response.status == 200
    finally:
        await hermes.stop()


async def test_shared_fixture_yields_a_running_server(fake_hermes: FakeHermes) -> None:
    status, chunks = await _stream(fake_hermes, {"X-Hermes-Session-Key": "stackchan-aa:bb:cc"})

    assert status == 200
    assert _text(chunks) == "Hello there"
    assert fake_hermes.requests[-1]["session_key"] == "stackchan-aa:bb:cc"
