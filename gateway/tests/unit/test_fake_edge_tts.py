"""Unit tests for the fake edge-tts synthesiser (T006).

edge-tts is a library, not a service, so the fake is a monkeypatchable object
rather than an HTTP server: T022 swaps it in and needs to fail one sentence
without failing the turn.
"""

from __future__ import annotations

import pytest

from tests.fakes.fake_edge_tts import FakeEdgeTTS, FakeTTSError


async def test_synthesize_returns_the_configured_audio_and_records_the_text() -> None:
    tts = FakeEdgeTTS(mp3_bytes=b"fake-mp3-bytes")

    audio = await tts.synthesize("Hello there.")

    assert audio == b"fake-mp3-bytes"
    assert tts.calls == ["Hello there."]


async def test_sentences_are_recorded_in_order() -> None:
    tts = FakeEdgeTTS()

    await tts.synthesize("One.")
    await tts.synthesize("Two.")

    assert tts.calls == ["One.", "Two."]


async def test_fail_on_raises_for_that_sentence_only() -> None:
    tts = FakeEdgeTTS(fail_on={"Two."})

    assert await tts.synthesize("One.")
    with pytest.raises(FakeTTSError):
        await tts.synthesize("Two.")
    assert await tts.synthesize("Three.")


async def test_synthesize_can_be_paced_to_simulate_slow_synthesis() -> None:
    tts = FakeEdgeTTS(delay_s=0.05)

    audio = await tts.synthesize("Slow one.")

    assert audio
    assert tts.calls == ["Slow one."]


async def test_shared_fixture_yields_a_fake(fake_edge_tts: FakeEdgeTTS) -> None:
    fake_edge_tts.fail_on = {"Nope."}

    assert await fake_edge_tts.synthesize("Yes.")
    with pytest.raises(FakeTTSError):
        await fake_edge_tts.synthesize("Nope.")
