"""Unit tests for the fake whisper upstream (T006).

The fake is the only STT the headless proof ever talks to, so its modes are the
contract for T020: success, empty result, HTTP error, and a slow response.
"""

from __future__ import annotations

import time

import aiohttp

from tests.fakes.fake_whisper import FakeWhisper

WAV_BYTES = b"RIFF$\x00\x00\x00WAVEfmt "


async def _post(whisper: FakeWhisper) -> tuple[int, dict]:
    form = aiohttp.FormData()
    form.add_field("file", WAV_BYTES, filename="utterance_ok.wav", content_type="audio/wav")
    form.add_field("model", "whisper-1")
    async with aiohttp.ClientSession() as session:
        async with session.post(whisper.url, data=form) as response:
            return response.status, await response.json()


async def test_success_mode_returns_the_configured_text() -> None:
    whisper = FakeWhisper(text="what time is it")
    await whisper.start()
    try:
        status, body = await _post(whisper)
    finally:
        await whisper.stop()

    assert status == 200
    assert body == {"text": "what time is it"}
    assert whisper.requests[-1]["path"] == "/v1/audio/transcriptions"
    assert whisper.requests[-1]["file_field"] == "file"
    assert whisper.requests[-1]["filename"] == "utterance_ok.wav"
    assert whisper.requests[-1]["model"] == "whisper-1"
    assert whisper.requests[-1]["body"] == WAV_BYTES


async def test_empty_mode_returns_empty_text() -> None:
    whisper = FakeWhisper(mode="empty", text="ignored")
    await whisper.start()
    try:
        status, body = await _post(whisper)
    finally:
        await whisper.stop()

    assert status == 200
    assert body == {"text": ""}


async def test_error_mode_returns_the_configured_status() -> None:
    whisper = FakeWhisper(mode="error", status=503)
    await whisper.start()
    try:
        status, body = await _post(whisper)
    finally:
        await whisper.stop()

    assert status == 503
    assert "error" in body


async def test_delay_mode_waits_before_answering() -> None:
    whisper = FakeWhisper(delay_s=0.25)
    await whisper.start()
    try:
        started = time.monotonic()
        status, _ = await _post(whisper)
        elapsed = time.monotonic() - started
    finally:
        await whisper.stop()

    assert status == 200
    assert elapsed >= 0.20


async def test_shared_fixture_yields_a_running_server(fake_whisper: FakeWhisper) -> None:
    assert fake_whisper.port > 0
    assert fake_whisper.url == f"http://127.0.0.1:{fake_whisper.port}/v1/audio/transcriptions"

    status, body = await _post(fake_whisper)

    assert status == 200
    assert body["text"] == "what time is it"
