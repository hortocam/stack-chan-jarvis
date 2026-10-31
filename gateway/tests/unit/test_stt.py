"""STT client against the whisper contract (T020, plan C6).

whisper on CT 912 is an OpenAI-compatible transcriber: a multipart WAV upload
comes back as ``{"text": ...}``. The client's job is narrow — frame the PCM as
a WAV in memory, post it inside the configured timeout, and turn every failure
into one typed error the turn can apologise for. Silence is not an error: an
empty transcript is a valid answer and the turn decides what it means.
"""

from __future__ import annotations

import io
import time
import wave

import pytest

from jarvis_gateway.audio import pcm16_silence
from jarvis_gateway.stt import SttClient, SttError

PCM = b"\x01\x00" * 24000  # 1.5 s of 16 kHz PCM16


async def test_uploads_a_16k_mono_pcm16_wav_and_returns_the_text(fake_whisper) -> None:
    client = SttClient(fake_whisper.base_url, timeout_s=5.0)

    text = await client.transcribe(PCM)

    assert text == "what time is it"
    request = fake_whisper.requests[0]
    assert request["path"] == "/v1/audio/transcriptions"
    assert request["file_field"] == "file"
    assert request["content_type"].startswith("multipart/")
    with wave.open(io.BytesIO(request["body"]), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16000
        assert handle.getnframes() == 24000


async def test_an_empty_transcript_is_a_result_not_an_error(fake_whisper) -> None:
    fake_whisper.mode = "empty"
    client = SttClient(fake_whisper.base_url, timeout_s=5.0)

    assert await client.transcribe(pcm16_silence(960)) == ""


async def test_text_is_stripped(fake_whisper) -> None:
    fake_whisper.text = "  hello there \n"
    client = SttClient(fake_whisper.base_url, timeout_s=5.0)

    assert await client.transcribe(PCM) == "hello there"


async def test_a_5xx_becomes_a_typed_error_naming_the_status(fake_whisper) -> None:
    fake_whisper.mode = "error"
    fake_whisper.status = 503
    client = SttClient(fake_whisper.base_url, timeout_s=5.0)

    with pytest.raises(SttError) as excinfo:
        await client.transcribe(PCM)

    assert "503" in str(excinfo.value)


async def test_the_configured_timeout_is_enforced(fake_whisper) -> None:
    fake_whisper.delay_s = 1.0
    client = SttClient(fake_whisper.base_url, timeout_s=0.15)

    started = time.monotonic()
    with pytest.raises(SttError) as excinfo:
        await client.transcribe(PCM)

    assert "timeout" in str(excinfo.value).lower()
    assert time.monotonic() - started < 1.0


async def test_an_absurdly_long_response_is_refused(fake_whisper) -> None:
    fake_whisper.text = "x" * 100_000
    client = SttClient(fake_whisper.base_url, timeout_s=5.0)

    with pytest.raises(SttError) as excinfo:
        await client.transcribe(PCM)

    assert "response" in str(excinfo.value).lower()


async def test_a_body_without_text_is_a_typed_error() -> None:
    """Guard the contract: ``{"text": ...}`` or nothing. A bare ``{}`` is not a transcript."""

    from aiohttp import web
    from aiohttp.test_utils import TestServer

    async def handler(request: web.Request) -> web.Response:
        await request.read()
        return web.json_response({"unexpected": 1})

    app = web.Application()
    app.router.add_post("/v1/audio/transcriptions", handler)
    server = TestServer(app)
    await server.start_server()
    try:
        client = SttClient(str(server.make_url("")).rstrip("/"), timeout_s=5.0)
        with pytest.raises(SttError) as excinfo:
            await client.transcribe(PCM)
    finally:
        await server.close()

    assert "text" in str(excinfo.value)
