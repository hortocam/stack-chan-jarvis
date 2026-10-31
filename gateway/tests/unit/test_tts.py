"""Sentence synthesis: edge-tts MP3 → 24 kHz PCM → 60 ms Opus frames (T022).

edge-tts is a library call returning MP3 bytes; the robot wants one Opus packet
per 60 ms at 24 kHz mono (plan C2). This module is the whole conversion, plus a
cancel hook the turn can pull between frames.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis_gateway.audio import DOWNLINK_FRAME_SAMPLES, DOWNLINK_RATE, OpusDecoder
from jarvis_gateway.tts import EdgeTts, TtsEngine, TtsError, mp3_to_pcm24, pcm24_to_frames
from tests.fakes.fake_edge_tts import FakeEdgeTTS, make_mp3


async def test_a_sentence_is_synthesised_and_framed_at_24k(fake_edge_tts) -> None:
    fake_edge_tts.mp3_bytes = make_mp3(1.0)
    engine = TtsEngine(fake_edge_tts)

    frames = [frame async for frame in engine.frames("Good evening, sir.")]

    assert fake_edge_tts.calls == ["Good evening, sir."]
    # 1.0 s of audio plus the MP3 encoder's delay/padding: 17–19 sixty-ms frames.
    assert 17 <= len(frames) <= 19
    decoder = OpusDecoder(DOWNLINK_RATE)
    for packet in frames:
        assert len(decoder.decode(packet)) == DOWNLINK_FRAME_SAMPLES * 2


async def test_cancellation_stops_mid_sentence(fake_edge_tts) -> None:
    fake_edge_tts.mp3_bytes = make_mp3(2.0)
    engine = TtsEngine(fake_edge_tts)
    cancel = asyncio.Event()
    received: list[bytes] = []

    async for frame in engine.frames("A long sentence that keeps going.", cancel=cancel):
        received.append(frame)
        if len(received) == 3:
            cancel.set()

    assert len(received) == 3


async def test_a_synthesiser_failure_is_a_typed_error(fake_edge_tts) -> None:
    fake_edge_tts.fail_on = {"Nope."}
    engine = TtsEngine(fake_edge_tts)

    with pytest.raises(TtsError) as excinfo:
        _ = [frame async for frame in engine.frames("Nope.")]

    assert "Nope." in str(excinfo.value)


async def test_undecodable_audio_is_a_typed_error() -> None:
    fake = FakeEdgeTTS(mp3_bytes=b"\xff\xfb\x90\x00not really an mp3 stream" * 4)
    engine = TtsEngine(fake)

    with pytest.raises(TtsError):
        _ = [frame async for frame in engine.frames("Broken.")]


def test_pcm_framing_pads_only_the_tail() -> None:
    pcm = bytes(DOWNLINK_FRAME_SAMPLES * 2 * 2 + 100)  # 2 frames and a bit

    frames = pcm24_to_frames(pcm)

    assert len(frames) == 3
    assert all(len(frame) == DOWNLINK_FRAME_SAMPLES * 2 for frame in frames)


def test_mp3_duration_survives_the_conversion() -> None:
    pcm = mp3_to_pcm24(make_mp3(1.0))

    # 1.0 s at 24 kHz plus the codec's short delay, never a truncated answer.
    assert 24_000 <= len(pcm) // 2 <= 26_500


async def test_the_real_adapter_refuses_empty_text() -> None:
    with pytest.raises(TtsError):
        await EdgeTts("en-GB-RyanNeural").synthesize("   ")
